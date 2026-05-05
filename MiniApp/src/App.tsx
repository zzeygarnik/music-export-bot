import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { AnimatePresence } from 'motion/react';
import { Track } from './types';
import { TrackItem } from './components/TrackItem';
import { PlayerBar } from './components/PlayerBar';
import { BottomNav } from './components/BottomNav';
import { FullPlayer } from './components/FullPlayer';
import { Snowflakes } from './components/Snowflakes';
import { Search, X } from 'lucide-react';
import { audioEngine } from './lib/audioEngine';

type RepeatMode = 0 | 1 | 2 | 3 | 'inf';

declare global {
  interface Window {
    Telegram?: { WebApp: { initData: string; ready: () => void; expand: () => void } };
  }
}

interface ApiTrack {
  file_id: string;
  artist: string;
  title: string;
  source: string;
  duration: number | null;
  thumb_id: string;
  custom_title?: string;
  custom_artist?: string;
  custom_cover_path?: string;
}

function formatDuration(sec: number | null | undefined): string {
  if (!sec) return '--:--';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function parseDurationStr(s: string): number {
  if (!s || s === '--:--') return 0;
  const parts = s.split(':');
  if (parts.length !== 2) return 0;
  const m = parseInt(parts[0], 10);
  const sec = parseInt(parts[1], 10);
  return isNaN(m) || isNaN(sec) ? 0 : m * 60 + sec;
}

function mapSource(src: string): Track['source'] {
  const map: Record<string, Track['source']> = {
    sc: 'SC', yt: 'YouTube', vk: 'SC',
    spotify: 'Spotify', ym: 'YM', tag_editor: 'SC',
  };
  return (map[src] ?? 'SC') as Track['source'];
}

const getInitData = () => window.Telegram?.WebApp?.initData ?? '';

function buildStreamUrl(trackId: string): string {
  const initData = getInitData();
  if (!initData) return '';
  return `/api/player/stream/${encodeURIComponent(trackId)}?tma=${encodeURIComponent(initData)}`;
}

// build:29
export default function App() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isPlayerOpen, setIsPlayerOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('library');
  const [currentTime, setCurrentTime] = useState(0);
  const [totalDuration, setTotalDuration] = useState(0);
  // True while fetching + decoding a track that wasn't preloaded
  const [buffering, setBuffering] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [isShuffle, setIsShuffle] = useState(false);
  const [repeatMode, setRepeatMode] = useState<RepeatMode>(0);
  // Initialised from localStorage via audioEngine.init() on first gesture; default 1 until then
  const [volume, setVolume] = useState(() => parseFloat(localStorage.getItem('zgrnk_volume') ?? '1'));
  const prevVolumeRef = useRef(1);

  // Stable refs for background callbacks (Android freezes JS, closures go stale)
  const tracksRef = useRef<Track[]>([]);
  useEffect(() => { tracksRef.current = tracks; }, [tracks]);
  const playingIdRef = useRef<string | null>(null);
  const isPlayingRef = useRef(false);
  useEffect(() => { isPlayingRef.current = isPlaying; }, [isPlaying]);
  const repeatModeRef = useRef<RepeatMode>(0);
  useEffect(() => { repeatModeRef.current = repeatMode; }, [repeatMode]);
  const isShuffleRef = useRef(false);
  useEffect(() => { isShuffleRef.current = isShuffle; }, [isShuffle]);
  const userWantsPlayRef = useRef(false);
  const handleNextRef = useRef<() => void>(() => {});
  const handlePrevRef = useRef<() => void>(() => {});
  const totalDurationRef = useRef(0);
  // Incremented on every playTrack call; async decode checks it to discard stale results
  const playRequestIdRef = useRef(0);
  useEffect(() => { totalDurationRef.current = totalDuration; }, [totalDuration]);

  const tgInitData = getInitData();
  const currentTrack = tracks[currentIdx] ?? tracks[0];

  // ------------------------------------------------------------------
  // Wire engine callbacks once on mount
  // ------------------------------------------------------------------
  useEffect(() => {
    audioEngine.onTimeUpdate = (t, d) => {
      setCurrentTime(t);
      if (d > 0) setTotalDuration(d);
    };
    audioEngine.onPlayStateChange = (playing) => {
      setIsPlaying(playing);
      isPlayingRef.current = playing;
    };
    audioEngine.onEnded = () => {
      const rm = repeatModeRef.current;
      if (rm === 'inf') {
        // Replay current buffer from start
        audioEngine.seek(0);
        audioEngine.resumePlayback().catch(() => {});
      } else if (rm > 0) {
        setRepeatMode(((rm as number) - 1) as RepeatMode);
        audioEngine.seek(0);
        audioEngine.resumePlayback().catch(() => {});
      } else {
        handleNextRef.current();
      }
    };
    return () => { audioEngine.stop(); };
  }, []);

  // ------------------------------------------------------------------
  // Media Session helpers
  // ------------------------------------------------------------------
  const updateMediaSession = useCallback((track: Track) => {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artist,
      artwork: track.coverUrl ? [{ src: track.coverUrl, sizes: '512x512', type: 'image/jpeg' }] : [],
    });
    navigator.mediaSession.playbackState = 'playing';
  }, []);

  // ------------------------------------------------------------------
  // Core playback
  // ------------------------------------------------------------------
  const playTrack = useCallback(async (idx: number, trackList?: Track[]) => {
    const list = trackList ?? tracksRef.current;
    const track = list[idx];
    if (!track) return;

    // Init / resume AudioContext — must be called in user gesture path
    audioEngine.init();

    setCurrentIdx(idx);
    playingIdRef.current = track.id;
    userWantsPlayRef.current = true;
    updateMediaSession(track);

    const preloaded = audioEngine.getPreloaded(idx);
    if (preloaded) {
      audioEngine.playBuffer(preloaded).catch(() => {});
      // Kick off preload for the track after this one
      const nextIdx = (idx + 1) % list.length;
      if (nextIdx !== idx && !isShuffleRef.current) {
        const url = buildStreamUrl(list[nextIdx].id);
        if (url) audioEngine.preload(nextIdx, url);
      }
      return;
    }

    // Not preloaded — stop old track immediately (no ghost), then fetch+decode.
    // setIsPlaying(true) right after stop() keeps Media Session in 'playing' state
    // so the OS doesn't send a spurious pause action during the buffering window.
    audioEngine.stop();
    setTotalDuration(0);
    setCurrentTime(0);
    setIsPlaying(true);
    isPlayingRef.current = true;
    const url = buildStreamUrl(track.id);
    if (!url) return;
    setBuffering(true);
    // Snapshot before async gap — if playTrack is called again for a different
    // track while we await, the ref increments and we discard the stale buffer.
    const requestId = ++playRequestIdRef.current;
    try {
      const buffer = await audioEngine.fetchAndDecode(url);
      if (playRequestIdRef.current !== requestId) return; // newer request won — drop it
      await audioEngine.playBuffer(buffer);
      // Set duration from buffer (precise, no metadata parsing needed)
      setTotalDuration(buffer.duration);
    } catch (_) {
      setIsPlaying(false);
      userWantsPlayRef.current = false;
    } finally {
      setBuffering(false);
    }

    // Preload next sequential track
    const nextIdx = (idx + 1) % list.length;
    if (nextIdx !== idx && !isShuffleRef.current) {
      const nextUrl = buildStreamUrl(list[nextIdx].id);
      if (nextUrl) audioEngine.preload(nextIdx, nextUrl);
    }
  }, [updateMediaSession]);

  const togglePlay = useCallback(async () => {
    if (!currentTrack) return;
    if (audioEngine.isPlaying) {
      audioEngine.pause();
      userWantsPlayRef.current = false;
    } else if (audioEngine.duration > 0) {
      // Buffer already loaded — just resume
      await audioEngine.resumePlayback();
      userWantsPlayRef.current = true;
    } else {
      // First press with no buffer yet — treat as play from start
      await playTrack(currentIdx);
    }
  }, [currentTrack, currentIdx, playTrack]);

  const handleTrackClick = (track: Track, idx: number) => {
    if (idx === currentIdx && audioEngine.isPlaying) { togglePlay(); }
    else { playTrack(idx); }
  };

  const handlePrev = useCallback(() => {
    playTrack(currentIdx > 0 ? currentIdx - 1 : tracksRef.current.length - 1);
  }, [currentIdx, playTrack]);

  const handleNext = useCallback(() => {
    const list = tracksRef.current;
    if (isShuffle && list.length > 1) {
      let r: number;
      do { r = Math.floor(Math.random() * list.length); } while (r === currentIdx);
      playTrack(r);
    } else {
      playTrack((currentIdx + 1) % list.length);
    }
  }, [currentIdx, isShuffle, playTrack]);

  useEffect(() => { handleNextRef.current = handleNext; }, [handleNext]);
  useEffect(() => { handlePrevRef.current = handlePrev; }, [handlePrev]);

  const handleSeek = (progress: number) => {
    const t = Math.max(0, Math.min(1, progress)) * audioEngine.duration;
    audioEngine.seek(t);
    setCurrentTime(t);
  };

  const handleVolumeChange = useCallback((v: number) => {
    if (v > 0) prevVolumeRef.current = v;
    setVolume(v);
    audioEngine.setVolume(v);
  }, []);

  const handleToggleMute = useCallback(() => {
    if (volume > 0) {
      prevVolumeRef.current = volume;
      setVolume(0);
      audioEngine.setVolume(0);
    } else {
      const restore = prevVolumeRef.current > 0 ? prevVolumeRef.current : 1;
      setVolume(restore);
      audioEngine.setVolume(restore);
    }
  }, [volume]);

  const handleToggleShuffle = useCallback(() => setIsShuffle(v => !v), []);
  const handleToggleRepeat = useCallback(() => {
    setRepeatMode(cur => {
      if (cur === 0) return 1;
      if (cur === 1) return 2;
      if (cur === 2) return 3;
      if (cur === 3) return 'inf';
      return 0;
    });
  }, []);

  // ------------------------------------------------------------------
  // Media Session action handlers
  // ------------------------------------------------------------------
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    ms.setActionHandler('play', () => {
      audioEngine.resumePlayback().catch(() => {});
      userWantsPlayRef.current = true;
    });
    ms.setActionHandler('pause', () => {
      audioEngine.pause();
      userWantsPlayRef.current = false;
    });
    ms.setActionHandler('nexttrack', () => handleNextRef.current());
    ms.setActionHandler('previoustrack', () => handlePrevRef.current());
    ms.setActionHandler('seekto', (e) => {
      if (e.seekTime != null) { audioEngine.seek(e.seekTime); setCurrentTime(e.seekTime); }
    });
    ms.setActionHandler('seekforward', (e) => {
      const t = Math.min(audioEngine.duration, audioEngine.currentTime + (e.seekOffset ?? 10));
      audioEngine.seek(t);
    });
    ms.setActionHandler('seekbackward', (e) => {
      const t = Math.max(0, audioEngine.currentTime - (e.seekOffset ?? 10));
      audioEngine.seek(t);
    });
  }, []);

  // Sync Media Session metadata when track changes
  useEffect(() => {
    if (currentTrack) updateMediaSession(currentTrack);
  }, [currentTrack, updateMediaSession]);

  // Sync playback state badge — keep 'playing' during buffering so OS doesn't send pause action
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.playbackState = (isPlaying || buffering) ? 'playing' : 'paused';
  }, [isPlaying, buffering]);

  // ------------------------------------------------------------------
  // Track list fetching
  // ------------------------------------------------------------------
  useEffect(() => {
    function doFetch() {
      const initData = getInitData();
      const tg = window.Telegram?.WebApp;
      if (tg) { tg.ready(); tg.expand(); }
      if (!initData) return;

      setLoading(true);
      fetch('/api/player/tracks?limit=500', { headers: { 'X-Tg-Init-Data': initData } })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then((data: ApiTrack[]) => {
          if (!data.length) { setTracks([]); return; }
          const mapped: Track[] = data.map(t => ({
            id: t.file_id,
            title: t.custom_title || t.title || 'Неизвестный трек',
            artist: t.custom_artist || t.artist || 'Неизвестный исполнитель',
            duration: formatDuration(t.duration),
            coverUrl: t.thumb_id
              ? `/api/player/thumb/${t.thumb_id}?tma=${encodeURIComponent(initData)}&fid=${encodeURIComponent(t.file_id)}${t.custom_cover_path ? `&t=${Date.now()}` : ''}`
              : t.custom_cover_path
                ? `/api/player/thumb/${t.file_id}?tma=${encodeURIComponent(initData)}&fid=${encodeURIComponent(t.file_id)}&t=${Date.now()}`
                : '',
            source: mapSource(t.source),
          }));
          setTracks(mapped);
          const playingId = playingIdRef.current;
          const keepIdx = playingId ? mapped.findIndex(t => t.id === playingId) : -1;
          if (keepIdx >= 0) setCurrentIdx(keepIdx);
          else setCurrentIdx(prev => (prev < mapped.length ? prev : 0));
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }

    doFetch();

    const tryResume = async () => {
      if (userWantsPlayRef.current && !audioEngine.isPlaying) {
        try { await audioEngine.resumePlayback(); } catch (_) {}
      }
    };
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      doFetch();
      tryResume();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', tryResume);
    window.addEventListener('pageshow', tryResume);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', tryResume);
      window.removeEventListener('pageshow', tryResume);
    };
  }, []);

  // ------------------------------------------------------------------
  // Fallback: set totalDuration from track metadata when engine has no buffer yet
  // ------------------------------------------------------------------
  useEffect(() => {
    if (totalDuration === 0 && currentTrack?.duration) {
      setTotalDuration(parseDurationStr(currentTrack.duration));
    }
  }, [currentTrack?.id]);

  // ------------------------------------------------------------------
  // Track management
  // ------------------------------------------------------------------
  const handleDelete = useCallback((fileId: string) => {
    const idx = tracks.findIndex(t => t.id === fileId);
    if (idx === currentIdx) {
      audioEngine.stop();
      setIsPlaying(false);
    }
    if (isPlayerOpen && idx === currentIdx) setIsPlayerOpen(false);
    setTracks(prev => prev.filter(t => t.id !== fileId));
    if (idx > 0 && idx <= currentIdx) setCurrentIdx(c => c - 1);
    const initData = getInitData();
    if (initData) {
      fetch(`/api/player/tracks/${encodeURIComponent(fileId)}`, {
        method: 'DELETE',
        headers: { 'X-Tg-Init-Data': initData },
      }).catch(() => {});
    }
  }, [tracks, currentIdx, isPlayerOpen]);

  const handleUpdateMeta = useCallback((fileId: string, updates: { title?: string; artist?: string; coverUrl?: string }) => {
    setTracks(prev => prev.map(t => t.id === fileId ? { ...t, ...updates } : t));
  }, []);

  const filteredTracks = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return tracks
      .map((t, i) => ({ track: t, idx: i }))
      .filter(({ track }) => !q || track.title.toLowerCase().includes(q) || track.artist.toLowerCase().includes(q));
  }, [tracks, searchQuery]);

  return (
    <div className="min-h-screen flex flex-col bg-surface overflow-x-hidden">
      <header className="fixed top-0 w-full z-50 flex items-center justify-between px-4 h-16 bg-[#0d0d14]/80 backdrop-blur-xl border-b border-white/5">
        <div className="w-10" />
        <div className="text-2xl font-black bg-gradient-to-r from-primary to-secondary-container bg-clip-text text-transparent tracking-tight">
          ZGRNK Music
        </div>
        <div className="w-10 flex justify-end">
          <span className="text-[10px] font-mono text-white/20 select-none">b29</span>
        </div>
      </header>

      {activeTab !== 'library' && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-surface pt-16 pb-20">
          <div className="flex flex-col items-center gap-4 text-on-surface-variant">
            <span className="text-5xl opacity-30">🚧</span>
            <p className="text-lg font-semibold">В разработке</p>
          </div>
        </div>
      )}

      <main className="flex-1 pt-20 pb-44 px-4 overflow-y-auto">
        {loading && (
          <p className="text-center text-on-surface-variant text-sm pt-8">Загружаем треки…</p>
        )}
        {!loading && tracks.length === 0 && (
          <div className="flex flex-col items-center gap-4 pt-24 text-on-surface-variant">
            <span className="text-5xl opacity-30">🎵</span>
            <p className="text-base font-semibold text-center px-8">
              Ты ещё не скачал ни одного трека через бота
            </p>
          </div>
        )}
        {activeTab === 'library' && (
          <div className="relative mb-4 max-w-2xl mx-auto">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant pointer-events-none" />
            <input
              type="text"
              placeholder="Поиск по треку или исполнителю…"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-9 py-2.5 rounded-xl bg-surface-container text-on-surface text-sm placeholder:text-on-surface-variant/50 outline-none focus:ring-1 focus:ring-primary/50"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-white">
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        )}
        <AnimatePresence>
          <div className="flex flex-col gap-2 max-w-2xl mx-auto">
            {filteredTracks.map(({ track, idx }) => (
              <TrackItem
                key={track.id}
                track={track}
                index={idx}
                isActive={currentIdx === idx && isPlaying}
                onClick={() => handleTrackClick(track, idx)}
                onDelete={handleDelete}
              />
            ))}
          </div>
        </AnimatePresence>
      </main>

      {!isPlayerOpen && currentTrack && (
        <PlayerBar
          track={currentTrack}
          isPlaying={isPlaying}
          currentTime={currentTime}
          totalDuration={totalDuration}
          onTogglePlay={togglePlay}
          onPrev={handlePrev}
          onNext={handleNext}
          onSeek={handleSeek}
          onOpenPlayer={() => setIsPlayerOpen(true)}
          buffering={buffering}
          volume={volume}
          onVolumeChange={handleVolumeChange}
          onToggleMute={handleToggleMute}
        />
      )}

      <BottomNav activeTab={activeTab} onTabChange={setActiveTab} />

      <AnimatePresence>
        {isPlayerOpen && currentTrack && (
          <FullPlayer
            track={currentTrack}
            isPlaying={isPlaying}
            currentTime={currentTime}
            totalDuration={totalDuration}
            onTogglePlay={togglePlay}
            onPrev={handlePrev}
            onNext={handleNext}
            onSeek={handleSeek}
            onClose={() => setIsPlayerOpen(false)}
            onDelete={handleDelete}
            isShuffle={isShuffle}
            repeatMode={repeatMode}
            onToggleShuffle={handleToggleShuffle}
            onToggleRepeat={handleToggleRepeat}
            onUpdateMeta={handleUpdateMeta}
            tgInitData={tgInitData}
            buffering={buffering}
            volume={volume}
            onVolumeChange={handleVolumeChange}
            onToggleMute={handleToggleMute}
          />
        )}
      </AnimatePresence>

      <Snowflakes />
      <div className="fixed inset-0 pointer-events-none -z-10 overflow-hidden">
        <div className="absolute top-[-10%] right-[-5%] w-[60vw] h-[60vw] bg-primary/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-[-10%] left-[-5%] w-[80vw] h-[80vw] bg-secondary-container/10 rounded-full blur-[150px]" />
      </div>
    </div>
  );
}
