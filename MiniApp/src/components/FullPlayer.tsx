import { useState, useRef, useEffect, useCallback } from 'react';
import { motion } from 'motion/react';
import { Track } from '../types';
import { ChevronDown, Heart, Shuffle, Repeat, Play, Pause, SkipBack, SkipForward, Volume2, VolumeX } from 'lucide-react';
import { MarqueeText } from './MarqueeText';

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function useLongPress(cb: () => void, ms = 380) {
  const firedRef = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const startPos = useRef<{ x: number; y: number } | null>(null);
  const start = useCallback((e: React.TouchEvent) => {
    firedRef.current = false;
    startPos.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    timer.current = setTimeout(() => {
      firedRef.current = true;
      // Haptic fires here (trusted timer context is fine for vibrate)
      try { navigator.vibrate?.(30); } catch (_) {}
    }, ms);
  }, [ms]);
  const end = useCallback(() => {
    clearTimeout(timer.current);
    startPos.current = null;
    if (firedRef.current) {
      firedRef.current = false;
      cb(); // runs in touchend = trusted user gesture → file picker / modal open reliably
    }
  }, [cb]);
  const move = useCallback((e: React.TouchEvent) => {
    if (!startPos.current) return;
    const dx = e.touches[0].clientX - startPos.current.x;
    const dy = e.touches[0].clientY - startPos.current.y;
    // Only cancel if finger actually moved >10px — prevents false cancels from micro-jitter
    if (Math.hypot(dx, dy) > 10) {
      clearTimeout(timer.current);
      firedRef.current = false;
      startPos.current = null;
    }
  }, []);
  return {
    onTouchStart: start,
    onTouchEnd: end,
    onTouchMove: move,
    onContextMenu: (e: React.MouseEvent) => e.preventDefault(),
  };
}

function compressCover(file: File): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const canvas = document.createElement('canvas');
      canvas.width = 500; canvas.height = 500;
      const ctx = canvas.getContext('2d')!;
      const size = Math.min(img.width, img.height);
      const sx = (img.width - size) / 2;
      const sy = (img.height - size) / 2;
      ctx.drawImage(img, sx, sy, size, size, 0, 0, 500, 500);
      canvas.toBlob(
        b => (b ? resolve(b) : reject(new Error('compress failed'))),
        'image/jpeg',
        0.85,
      );
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('load failed')); };
    img.src = url;
  });
}

type RepeatMode = 0 | 1 | 2 | 3 | 'inf';

interface FullPlayerProps {
  track: Track;
  isPlaying: boolean;
  currentTime: number;
  totalDuration: number;
  onTogglePlay: () => void;
  onPrev: () => void;
  onNext: () => void;
  onSeek: (progress: number) => void;
  onClose: () => void;
  onDelete: (fileId: string) => void;
  isShuffle: boolean;
  repeatMode: RepeatMode;
  onToggleShuffle: () => void;
  onToggleRepeat: () => void;
  onUpdateMeta: (fileId: string, updates: { title?: string; artist?: string; coverUrl?: string }) => void;
  tgInitData: string;
  buffering?: boolean;
  volume: number;
  onVolumeChange: (v: number) => void;
  onToggleMute: () => void;
}

export function FullPlayer({
  track, isPlaying, currentTime, totalDuration,
  onTogglePlay, onPrev, onNext, onSeek, onClose, onDelete,
  isShuffle, repeatMode, onToggleShuffle, onToggleRepeat,
  onUpdateMeta, tgInitData, buffering, volume, onVolumeChange, onToggleMute,
}: FullPlayerProps) {
  const [imgErr, setImgErr] = useState(false);
  useEffect(() => { setImgErr(false); }, [track.id]);

  // Edit metadata state
  const [editMode, setEditMode] = useState<null | 'title' | 'artist'>(null);
  const [editValue, setEditValue] = useState('');
  const [saving, setSaving] = useState(false);
  const coverInputRef = useRef<HTMLInputElement>(null);

  const openEdit = useCallback((field: 'title' | 'artist') => {
    setEditValue(field === 'title' ? track.title : track.artist);
    setEditMode(field);
  }, [track.title, track.artist]);

  const submitText = useCallback(async () => {
    if (!editMode || saving) return;
    setSaving(true);
    try {
      const fd = new FormData();
      fd.append(editMode, editValue.trim());
      const resp = await fetch(`/api/player/tracks/${encodeURIComponent(track.id)}/meta`, {
        method: 'POST',
        headers: { 'X-Tg-Init-Data': tgInitData },
        body: fd,
      });
      if (resp.ok) {
        onUpdateMeta(track.id, editMode === 'title' ? { title: editValue.trim() } : { artist: editValue.trim() });
        setEditMode(null);
      }
    } finally {
      setSaving(false);
    }
  }, [editMode, editValue, saving, track.id, tgInitData, onUpdateMeta]);

  const onCoverFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    setSaving(true);
    try {
      const blob = await compressCover(file);
      const fd = new FormData();
      fd.append('cover', blob, 'cover.jpg');
      const resp = await fetch(`/api/player/tracks/${encodeURIComponent(track.id)}/meta`, {
        method: 'POST',
        headers: { 'X-Tg-Init-Data': tgInitData },
        body: fd,
      });
      if (resp.ok) {
        let ts: number | undefined;
        try { const d = await resp.json(); ts = d.ts; } catch (_) {}
        // Only update UI when server confirmed the cover was saved (ts present)
        if (ts != null) {
          setImgErr(false);
          onUpdateMeta(track.id, {
            coverUrl: `/api/player/thumb/${encodeURIComponent(track.id)}?tma=${encodeURIComponent(tgInitData)}&t=${ts}`,
          });
        }
      }
    } finally {
      setSaving(false);
    }
  }, [track.id, tgInitData, onUpdateMeta]);

  const titleLongPress = useLongPress(useCallback(() => openEdit('title'), [openEdit]));
  const artistLongPress = useLongPress(useCallback(() => openEdit('artist'), [openEdit]));
  const coverLongPress = useLongPress(useCallback(() => coverInputRef.current?.click(), []));
  // During drag show local position; null means use engine time
  const [dragProgress, setDragProgress] = useState<number | null>(null);
  const dragProgressRef = useRef<number | null>(null);
  const engineProgress = totalDuration > 0 ? Math.min(currentTime / totalDuration, 1) : 0;
  const progress = dragProgress !== null ? dragProgress : engineProgress;

  const progressBarRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);
  const onSeekRef = useRef(onSeek);
  useEffect(() => { onSeekRef.current = onSeek; }, [onSeek]);

  const calcProgress = (clientX: number) => {
    const rect = progressBarRef.current?.getBoundingClientRect();
    if (!rect) return null;
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  };

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isDraggingRef.current) return;
      const p = calcProgress(e.clientX);
      if (p !== null) { setDragProgress(p); dragProgressRef.current = p; }
    };
    const onMouseUp = () => {
      if (isDraggingRef.current && dragProgressRef.current !== null) {
        onSeekRef.current(dragProgressRef.current);
      }
      isDraggingRef.current = false;
      setDragProgress(null);
      dragProgressRef.current = null;
    };
    const onTouchMove = (e: TouchEvent) => {
      if (!isDraggingRef.current) return;
      e.preventDefault();
      const p = calcProgress(e.touches[0].clientX);
      if (p !== null) { setDragProgress(p); dragProgressRef.current = p; }
    };
    const onTouchEnd = () => {
      if (isDraggingRef.current && dragProgressRef.current !== null) {
        onSeekRef.current(dragProgressRef.current);
      }
      isDraggingRef.current = false;
      setDragProgress(null);
      dragProgressRef.current = null;
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('touchend', onTouchEnd);
    return () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend', onTouchEnd);
    };
  }, []);

  const handlePointerDown = (clientX: number) => {
    isDraggingRef.current = true;
    const p = calcProgress(clientX);
    if (p !== null) { setDragProgress(p); dragProgressRef.current = p; }
  };

  const handleDelete = () => {
    onDelete(track.id);
    onClose();
  };

  return (
    <motion.div
      initial={{ y: '100%' }}
      animate={{ y: 0 }}
      exit={{ y: '100%' }}
      transition={{ type: 'spring', damping: 25, stiffness: 200 }}
      className="fixed inset-x-0 top-0 z-[100] bg-surface flex flex-col pt-8 overflow-hidden"
      style={{ height: '100dvh' }}
    >
      <div className="absolute inset-0 z-0 pointer-events-none opacity-30">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[80vw] h-[80vw] bg-primary-container rounded-full blur-[100px] opacity-20" />
      </div>

      <header className="relative z-10 flex justify-between items-center px-4 mb-4 shrink-0">
        <button onClick={onClose} className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-white/10 active:scale-90 transition-transform">
          <ChevronDown className="w-6 h-6" />
        </button>
        <h1 className="text-lg font-bold text-white tracking-widest uppercase">Now Playing</h1>
        <div className="w-10" />
      </header>

      <main className="relative z-10 flex-1 flex flex-col items-center px-8 min-h-0 overflow-hidden">
        <input
          ref={coverInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={onCoverFileChange}
        />
        <motion.div
          layoutId="album-art"
          className="mb-4 relative rounded-2xl overflow-hidden glass shadow-[0_0_50px_rgba(0,0,0,0.5)] shrink-0"
          style={{ aspectRatio: '1 / 1', width: 'min(100%, 36vh)', alignSelf: 'center', WebkitTouchCallout: 'none', userSelect: 'none' } as React.CSSProperties}
          {...coverLongPress}
        >
          {track.coverUrl && !imgErr ? (
            <img
              src={track.coverUrl}
              alt={track.title}
              className="w-full h-full object-cover pointer-events-none"
              onError={() => setImgErr(true)}
              draggable={false}
            />
          ) : (
            <div className="w-full h-full bg-gradient-to-br from-primary-container/30 to-secondary-container/30 flex items-center justify-center">
              <span className="text-[6rem] opacity-20">🎵</span>
            </div>
          )}
          {saving && (
            <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
              <div className="w-8 h-8 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            </div>
          )}
        </motion.div>

        <div className="w-full flex justify-between items-center mb-4 shrink-0">
          <div className="flex flex-col gap-1 min-w-0 flex-1 mr-4">
            <div
              style={{ WebkitUserSelect: 'none', userSelect: 'none', WebkitTouchCallout: 'none' } as React.CSSProperties}
              {...titleLongPress}
            >
              <MarqueeText text={track.title} isPlaying={isPlaying} className="text-3xl font-bold text-on-surface" />
            </div>
            <div
              style={{ WebkitUserSelect: 'none', userSelect: 'none', WebkitTouchCallout: 'none' } as React.CSSProperties}
              {...artistLongPress}
            >
              <p className="text-lg text-on-surface-variant truncate">{track.artist}</p>
            </div>
          </div>
          <button
            onClick={handleDelete}
            className="text-primary hover:text-red-400 transition-colors active:scale-90"
          >
            <Heart className="w-7 h-7 fill-current" />
          </button>
        </div>

        <div className="w-full mb-4 shrink-0">
          <div
            ref={progressBarRef}
            className="h-8 flex items-center w-full cursor-pointer group touch-none"
            onMouseDown={(e) => { e.stopPropagation(); handlePointerDown(e.clientX); }}
            onTouchStart={(e) => { e.stopPropagation(); handlePointerDown(e.touches[0].clientX); }}
          >
            <div className="h-1.5 w-full bg-surface-container-highest rounded-full relative pointer-events-none">
              <div
                className="absolute top-0 left-0 h-full bg-gradient-to-r from-primary-container to-primary rounded-full relative"
                style={{ width: `${progress * 100}%` }}
              >
                <div className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 w-4 h-4 bg-white rounded-full shadow-[0_0_15px_rgba(183,109,255,1)] opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            </div>
          </div>
          <div className="flex justify-between w-full mt-3 text-xs font-semibold text-on-surface-variant">
            <span>{fmt(currentTime)}</span>
            <span>{totalDuration > 0 ? fmt(totalDuration) : track.duration}</span>
          </div>
        </div>

        {/* Compact volume row — centered, not full-width */}
        <div className="flex items-center gap-2 mb-4 shrink-0">
          <button
            onClick={onToggleMute}
            className="text-on-surface-variant hover:text-white transition-colors shrink-0"
          >
            {volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={volume}
            onChange={e => onVolumeChange(parseFloat(e.target.value))}
            className="h-1.5 cursor-pointer"
            style={{ accentColor: 'var(--color-primary)', width: '120px' }}
          />
          <span className="text-xs font-semibold text-on-surface-variant w-7 text-right shrink-0">
            {Math.round(volume * 100)}%
          </span>
        </div>

        <div className="w-full flex items-center justify-between px-2 shrink-0">
          <button
            onClick={onToggleShuffle}
            className={`relative transition-colors ${isShuffle ? 'text-primary' : 'text-on-surface-variant hover:text-primary'}`}
          >
            <Shuffle className="w-6 h-6" />
            {isShuffle && <span className="absolute -top-1.5 -right-1.5 w-2 h-2 bg-primary rounded-full" />}
          </button>
          <button onClick={onPrev} className="text-white hover:text-primary transition-all active:scale-90">
            <SkipBack className="w-10 h-10 fill-current" />
          </button>
          <button
            onClick={onTogglePlay}
            className="w-20 h-20 rounded-full bg-gradient-to-br from-primary to-primary-container flex items-center justify-center text-white shadow-[0_0_30px_rgba(183,109,255,0.4)] active:scale-95 transition-transform"
          >
            {buffering
              ? <div className="w-8 h-8 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              : isPlaying
                ? <Pause className="w-10 h-10 fill-current" />
                : <Play className="w-10 h-10 fill-current ml-2" />}
          </button>
          <button onClick={onNext} className="text-white hover:text-primary transition-all active:scale-90">
            <SkipForward className="w-10 h-10 fill-current" />
          </button>
          <div className="relative">
            <button
              onClick={onToggleRepeat}
              className={`transition-colors ${repeatMode !== 0 ? 'text-primary' : 'text-on-surface-variant hover:text-primary'}`}
            >
              <Repeat className="w-6 h-6" />
            </button>
            {repeatMode !== 0 && (
              <span className="absolute -top-2 -right-2 text-[10px] font-bold text-primary leading-none pointer-events-none">
                {repeatMode === 'inf' ? '∞' : repeatMode}
              </span>
            )}
          </div>
        </div>
      </main>

      <div className="pb-4 shrink-0" />

      {/* Rename modal */}
      {editMode && (
        <div className="absolute inset-0 z-20 flex items-end" onClick={() => setEditMode(null)}>
          <div
            className="w-full bg-surface-container-high rounded-t-2xl p-6 flex flex-col gap-4"
            onClick={e => e.stopPropagation()}
          >
            <p className="text-sm font-semibold text-on-surface-variant uppercase tracking-widest">
              {editMode === 'title' ? 'Edit Title' : 'Edit Artist'}
            </p>
            <input
              autoFocus
              className="w-full bg-surface-container text-on-surface text-lg rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-primary/60"
              value={editValue}
              onChange={e => setEditValue(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') submitText(); if (e.key === 'Escape') setEditMode(null); }}
            />
            <div className="flex gap-3">
              <button
                onClick={() => setEditMode(null)}
                className="flex-1 py-3 rounded-xl bg-surface-container text-on-surface-variant font-semibold"
              >
                Cancel
              </button>
              <button
                onClick={submitText}
                disabled={saving || !editValue.trim()}
                className="flex-1 py-3 rounded-xl bg-primary text-on-primary font-semibold disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </motion.div>
  );
}
