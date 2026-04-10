'use client';

// ─────────────────────────────────────────────────────────
//  AttachmentPreview — File attachment cards + lightbox
//  Handles image/video/figma previews above the chat input
// ─────────────────────────────────────────────────────────

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { X, Eye, Loader2 } from 'lucide-react';

function formatFileSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── Single attachment card ────────────────────────────────
function AttachmentCard({ img, index, onRemove, onPreview }) {
  return (
    <div className="relative group flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-2 py-1.5 hover:bg-slate-100 transition-colors">
      {/* Thumbnail */}
      <div className="relative w-10 h-10 rounded-lg overflow-hidden shrink-0 bg-slate-200">
        {img.data ? (
          <img src={img.data} alt={img.name} className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
          </div>
        )}
        {img.data && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onPreview(img); }}
            className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Eye className="w-3.5 h-3.5 text-white" />
          </button>
        )}
      </div>
      {/* File info */}
      <div className="flex flex-col min-w-0">
        <span className="text-[11px] font-medium text-slate-700 truncate max-w-[100px]">{img.name}</span>
        <span className="text-[10px] text-slate-400">{formatFileSize(img.size)}</span>
      </div>
      {/* Remove */}
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="ml-1 p-0.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ── Image lightbox modal ──────────────────────────────────
function ImageLightbox({ image, onClose }) {
  if (!image) return null;
  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div className="relative max-w-[80vw] max-h-[80vh]" onClick={(e) => e.stopPropagation()}>
        <img
          src={image.data}
          alt={image.name}
          className="max-w-full max-h-[80vh] rounded-xl shadow-2xl"
        />
        <button
          onClick={onClose}
          className="absolute -top-3 -right-3 w-8 h-8 bg-white text-slate-600 rounded-full flex items-center justify-center shadow-lg hover:bg-slate-100 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent rounded-b-xl px-4 py-3">
          <p className="text-white text-sm font-medium">{image.name}</p>
          <p className="text-white/70 text-xs">{formatFileSize(image.size)}</p>
        </div>
      </div>
    </div>
  );
}

// ── Public component ──────────────────────────────────────
export default function AttachmentPreview({ attachedImages, onRemove, onAddMore }) {
  const [previewImage, setPreviewImage] = useState(null);

  if (attachedImages.length === 0) return null;

  return (
    <>
      <div className="px-4 pt-3 pb-1">
        <div className="flex gap-2 flex-wrap">
          {attachedImages.map((img, i) => (
            <AttachmentCard
              key={i}
              img={img}
              index={i}
              onRemove={onRemove}
              onPreview={setPreviewImage}
            />
          ))}
          {attachedImages.length < 5 && (
            <button
              type="button"
              onClick={onAddMore}
              className="flex items-center justify-center w-10 h-[52px] border border-dashed border-slate-300 rounded-xl text-slate-400 hover:text-slate-600 hover:border-slate-400 transition-colors"
              title="Add more images"
            >
              <span className="text-lg font-light">+</span>
            </button>
          )}
        </div>
      </div>

      <ImageLightbox image={previewImage} onClose={() => setPreviewImage(null)} />
    </>
  );
}
