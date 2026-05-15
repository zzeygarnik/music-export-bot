import { memo, useRef, useState, useEffect } from 'react';
import { motion, PanInfo, useMotionValue, animate } from 'motion/react';
import { Track } from '../types';
import { cn } from '../lib/utils';
import { BarChart3, Trash2 } from 'lucide-react';

interface TrackItemProps {
  track: Track;
  index: number;
  isActive: boolean;
  onPlay: (track: Track, idx: number) => void;
  onDelete: (fileId: string) => void;
}

export const TrackItem = memo(function TrackItem({ track, index, isActive, onPlay, onDelete }: TrackItemProps) {
  const xMV = useMotionValue(0);
  const dragging = useRef(false);
  const [imgErr, setImgErr] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  useEffect(() => { setImgErr(false); setImgLoaded(false); }, [track.id]);
  const REVEAL = -76;

  const snapTo = (target: number) =>
    animate(xMV, target, { type: 'spring', stiffness: 600, damping: 40, mass: 0.4 });

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    const flickOpen  = info.velocity.x < -200;
    const flickClose = info.velocity.x >  200;
    const pastHalf   = info.offset.x < REVEAL / 2;
    snapTo(flickClose ? 0 : flickOpen || pastHalf ? REVEAL : 0);
    setTimeout(() => { dragging.current = false; }, 50);
  };

  return (
    // Hardware-accelerated container — prevents flicker on iOS WebView during virtual scroll
    <div
      className="relative rounded-xl overflow-hidden"
      style={{ transform: 'translateZ(0)', backfaceVisibility: 'hidden' }}
    >
      <button
        onClick={() => onDelete(track.id)}
        className="absolute inset-y-0 right-0 w-20 flex items-center justify-center bg-red-500/90 active:bg-red-600"
      >
        <Trash2 className="w-5 h-5 text-white" />
      </button>

      <motion.div
        drag="x"
        dragConstraints={{ left: REVEAL, right: 0 }}
        dragElastic={0}
        onDragStart={() => { dragging.current = true; }}
        onDragEnd={handleDragEnd}
        onClick={() => { if (!dragging.current) onPlay(track, index); }}
        style={{ x: xMV, touchAction: 'pan-y', backgroundColor: 'var(--color-surface)' }}
        className={cn(
          "relative z-10 flex items-center p-4 gap-4 cursor-pointer transition-colors duration-300",
          isActive ? "active-glass" : "glass hover:bg-white/5"
        )}
      >
        <div className="w-10 h-10 flex-shrink-0 relative">
          {track.coverUrl && !imgErr ? (
            <>
              {!imgLoaded && (
                <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-primary-container/50 to-secondary-container/50 flex items-center justify-center">
                  <span className="text-base">🎵</span>
                </div>
              )}
              <img
                src={track.coverUrl}
                alt={track.title}
                className={imgLoaded ? 'w-10 h-10 rounded-lg object-cover' : 'hidden'}
                onLoad={() => setImgLoaded(true)}
                onError={() => setImgErr(true)}
              />
            </>
          ) : (
            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-primary-container/50 to-secondary-container/50 flex items-center justify-center">
              <span className="text-base">🎵</span>
            </div>
          )}
          {isActive && (
            <div className="absolute inset-0 rounded-lg bg-black/50 flex items-center justify-center">
              <BarChart3 className="w-5 h-5 text-primary animate-pulse" />
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className={cn("text-base font-semibold truncate", isActive ? "text-primary" : "text-on-surface")}>
            {track.title}
          </h3>
          <p className="text-sm text-on-surface-variant truncate">{track.artist}</p>
        </div>
        <span className="flex-shrink-0 text-xs text-on-surface-variant tabular-nums">{track.duration}</span>
      </motion.div>
    </div>
  );
});
