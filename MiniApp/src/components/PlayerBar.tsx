import { useState, useEffect, useRef } from 'react';
import { motion } from 'motion/react';
import { Track } from '../types';
import { Play, Pause, SkipBack, SkipForward, Volume2, VolumeX } from 'lucide-react';
import { MarqueeText } from './MarqueeText';

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

interface PlayerBarProps {
  track: Track;
  isPlaying: boolean;
  currentTime: number;
  totalDuration: number;
  onTogglePlay: () => void;
  onPrev: () => void;
  onNext: () => void;
  onSeek: (progress: number) => void;
  onOpenPlayer: () => void;
  buffering?: boolean;
  volume: number;
  onVolumeChange: (v: number) => void;
  onToggleMute: () => void;
}

export function PlayerBar({
  track, isPlaying, currentTime, totalDuration,
  onTogglePlay, onPrev, onNext, onSeek, onOpenPlayer, buffering,
  volume, onVolumeChange, onToggleMute,
}: PlayerBarProps) {
  const [imgErr, setImgErr] = useState(false);
  useEffect(() => { setImgErr(false); }, [track.id, track.coverUrl]);

  // During drag show local position; null means use engine time
  const [dragProgress, setDragProgress] = useState<number | null>(null);
  const dragProgressRef = useRef<number | null>(null);
  const engineProgress = totalDuration > 0 ? Math.min((currentTime / totalDuration) * 100, 100) : 0;
  const progress = dragProgress !== null ? dragProgress * 100 : engineProgress;

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

  const handlePointerDown = (e: React.MouseEvent | React.TouchEvent, clientX: number) => {
    e.stopPropagation();
    isDraggingRef.current = true;
    const p = calcProgress(clientX);
    if (p !== null) { setDragProgress(p); dragProgressRef.current = p; }
  };

  return (
    <motion.div
      layoutId="player-container"
      className="fixed bottom-24 left-4 right-4 z-40"
    >
      <div style={{ backgroundColor: 'var(--color-surface)' }} className="glass rounded-2xl p-4 shadow-2xl shadow-black/50" onClick={onOpenPlayer}>
        <div className="flex flex-col gap-2 mb-3">
          {/* tall hit area for drag, visually shows thin bar */}
          <div
            ref={progressBarRef}
            className="h-8 flex items-center cursor-pointer touch-none"
            onMouseDown={(e) => handlePointerDown(e, e.clientX)}
            onTouchStart={(e) => handlePointerDown(e, e.touches[0].clientX)}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="h-1 w-full bg-surface-container-highest rounded-full overflow-hidden relative pointer-events-none">
              <div
                className="absolute top-0 left-0 h-full bg-primary shadow-[0_0_8px_rgba(183,109,255,0.8)] "
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
          <div className="flex justify-between items-center px-1">
            <span className="text-[10px] font-semibold text-primary">{fmt(currentTime)}</span>
            <span className="text-[10px] font-semibold text-on-surface-variant">
              {totalDuration > 0 ? fmt(totalDuration) : track.duration}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="w-14 h-14 rounded-lg overflow-hidden border border-white/10 shrink-0">
            {track.coverUrl && !imgErr ? (
              <img
                src={track.coverUrl}
                alt={track.title}
                className="w-full h-full object-cover"
                onError={() => setImgErr(true)}
              />
            ) : (
              <div className="w-full h-full bg-gradient-to-br from-primary-container/50 to-secondary-container/50 flex items-center justify-center">
                <span className="text-xl">🎵</span>
              </div>
            )}
          </div>

          <div className="flex-1 min-w-0">
            <MarqueeText text={track.title} isPlaying={isPlaying} className="text-base font-bold text-on-surface" />
            <p className="text-sm text-on-surface-variant truncate">{track.artist}</p>
          </div>

          <div className="flex flex-col items-center gap-1 shrink-0">
            <div className="flex items-center gap-2">
              <button
                onClick={(e) => { e.stopPropagation(); onPrev(); }}
                className="p-2 text-on-surface-variant hover:text-white transition-colors"
              >
                <SkipBack className="w-5 h-5 fill-current" />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onTogglePlay(); }}
                className="w-12 h-12 flex items-center justify-center rounded-full bg-gradient-to-br from-primary-container to-secondary-container text-white shadow-lg active:scale-90 transition-transform"
              >
                {buffering
                  ? <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  : isPlaying
                    ? <Pause className="w-6 h-6 fill-current" />
                    : <Play className="w-6 h-6 fill-current ml-1" />}
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onNext(); }}
                className="p-2 text-on-surface-variant hover:text-white transition-colors"
              >
                <SkipForward className="w-5 h-5 fill-current" />
              </button>
            </div>
            {/* Volume row centered under playback buttons */}
            <div
              className="flex items-center gap-2"
              onClick={e => e.stopPropagation()}
            >
              <button
                onClick={e => { e.stopPropagation(); onToggleMute(); }}
                className="text-on-surface-variant hover:text-white transition-colors shrink-0"
              >
                {volume === 0 ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={volume}
                onChange={e => onVolumeChange(parseFloat(e.target.value))}
                className="h-1 accent-primary cursor-pointer"
                style={{ accentColor: 'var(--color-primary)', width: '90px' }}
              />
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
