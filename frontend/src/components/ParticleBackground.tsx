/**
 * ParticleBackground — ambient enterprise-graph field.
 *
 * A sparse set of nodes drifts extremely slowly; nearby nodes join with
 * hairline connections that fade with distance (connect / disconnect as
 * they drift). Rendered on a single canvas for a steady 60 fps, DPR-aware,
 * and paused entirely under prefers-reduced-motion.
 *
 * Intensity is deliberately low: this is atmosphere, not spectacle.
 */
import React, { useEffect, useRef } from "react";

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  /** phase offset for the slow luminance breathing */
  phase: number;
}

export interface ParticleBackgroundProps {
  /** particles per 100k px² (default keeps ~60 on a laptop screen) */
  density?: number;
  /** master opacity of the whole layer */
  opacity?: number;
  /** px distance under which two nodes connect */
  linkDistance?: number;
}

const BLUE = "15, 98, 254"; // #0F62FE
const ACCENT = "77, 163, 255"; // #4DA3FF

const ParticleBackground: React.FC<ParticleBackgroundProps> = ({
  density = 0.55,
  opacity = 1,
  linkDistance = 150,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let raf = 0;
    let particles: Particle[] = [];
    let w = 0;
    let h = 0;

    const seed = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const count = Math.round(((w * h) / 100_000) * density * 10);
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        // extremely slow drift: ~4 px/second
        vx: (Math.random() - 0.5) * 0.14,
        vy: (Math.random() - 0.5) * 0.14,
        r: 0.8 + Math.random() * 1.4,
        phase: Math.random() * Math.PI * 2,
      }));
    };

    const draw = (t: number) => {
      ctx.clearRect(0, 0, w, h);

      // hairline connections first (under the nodes)
      for (let i = 0; i < particles.length; i++) {
        const a = particles[i];
        for (let j = i + 1; j < particles.length; j++) {
          const b = particles[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < linkDistance * linkDistance) {
            const alpha = (1 - Math.sqrt(d2) / linkDistance) * 0.10;
            ctx.strokeStyle = `rgba(${BLUE}, ${alpha})`;
            ctx.lineWidth = 0.6;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      for (const p of particles) {
        const breathe = 0.55 + 0.45 * Math.sin(t / 4200 + p.phase);
        const isAccent = p.phase > Math.PI * 1.72; // ~14% of nodes
        ctx.fillStyle = `rgba(${isAccent ? ACCENT : BLUE}, ${0.16 + 0.22 * breathe})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();

        p.x += p.vx;
        p.y += p.vy;
        if (p.x < -8) p.x = w + 8;
        if (p.x > w + 8) p.x = -8;
        if (p.y < -8) p.y = h + 8;
        if (p.y > h + 8) p.y = -8;
      }
    };

    const loop = (t: number) => {
      draw(t);
      raf = requestAnimationFrame(loop);
    };

    seed();
    if (reduced) {
      draw(0); // single static frame
    } else {
      raf = requestAnimationFrame(loop);
    }

    const onResize = () => seed();
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
    };
  }, [density, linkDistance]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        opacity,
        display: "block",
      }}
    />
  );
};

export default ParticleBackground;
