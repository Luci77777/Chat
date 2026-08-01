/*
 * Full-screen message effects — confetti / balloons / fireworks / slam / loud.
 * Exposes window.PingbackEffects.play(name, bubbleRowEl).
 *
 * confetti/balloons/fireworks run on a single shared full-screen <canvas>
 * (created lazily, reused across plays, cleared when idle) so replaying
 * effects back-to-back never stacks up canvases. slam/loud instead animate
 * the specific message bubble element via CSS classes defined in style.css
 * (.fx-slam / .fx-loud) — no canvas needed for those.
 */
(function () {
  const DURATION_MS = 2200;

  let canvas = null;
  let ctx = null;
  let rafId = null;
  let particles = [];
  let dpr = 1;

  function ensureCanvas() {
    if (canvas) return;
    canvas = document.createElement('canvas');
    canvas.id = 'effects-canvas';
    document.body.appendChild(canvas);
    ctx = canvas.getContext('2d');
    resize();
    window.addEventListener('resize', resize);
  }

  function resize() {
    if (!canvas) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    canvas.style.width = window.innerWidth + 'px';
    canvas.style.height = window.innerHeight + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function rand(min, max) { return Math.random() * (max - min) + min; }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

  const CONFETTI_COLORS = ['#6C4CF1', '#FF6B57', '#16C98B', '#FFC857', '#4FACFE', '#E85D9E'];

  function spawnConfetti() {
    const w = window.innerWidth;
    const count = 140;
    for (let i = 0; i < count; i++) {
      particles.push({
        kind: 'confetti',
        x: rand(0, w),
        y: rand(-40, -10),
        vx: rand(-1.2, 1.2),
        vy: rand(2, 5),
        size: rand(6, 11),
        rot: rand(0, Math.PI * 2),
        vr: rand(-0.2, 0.2),
        color: pick(CONFETTI_COLORS),
        shape: Math.random() > 0.5 ? 'rect' : 'circle',
        born: performance.now(),
      });
    }
  }

  function spawnBalloons() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const count = 12;
    for (let i = 0; i < count; i++) {
      particles.push({
        kind: 'balloon',
        x: rand(w * 0.08, w * 0.92),
        y: h + rand(20, 160),
        vx: rand(-0.3, 0.3),
        vy: rand(-2.6, -1.6),
        size: rand(26, 38),
        color: pick(CONFETTI_COLORS),
        sway: rand(0, Math.PI * 2),
        born: performance.now(),
      });
    }
  }

  function spawnFireworks() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const bursts = 4;
    for (let b = 0; b < bursts; b++) {
      const cx = rand(w * 0.2, w * 0.8);
      const cy = rand(h * 0.15, h * 0.5);
      const color = pick(CONFETTI_COLORS);
      const delay = b * 260;
      const sparks = 46;
      for (let i = 0; i < sparks; i++) {
        const angle = (Math.PI * 2 * i) / sparks;
        const speed = rand(2.4, 5.2);
        particles.push({
          kind: 'spark',
          x: cx, y: cy,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          size: rand(2, 3.5),
          color,
          born: performance.now() + delay,
          delay,
        });
      }
    }
  }

  function drawConfetti(p, t) {
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(p.rot);
    ctx.fillStyle = p.color;
    ctx.globalAlpha = Math.max(0, 1 - t / DURATION_MS);
    if (p.shape === 'rect') ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
    else { ctx.beginPath(); ctx.arc(0, 0, p.size / 2, 0, Math.PI * 2); ctx.fill(); }
    ctx.restore();
  }

  function drawBalloon(p, t) {
    const sway = Math.sin(p.sway + t / 400) * 14;
    ctx.save();
    ctx.globalAlpha = Math.max(0, 1 - Math.max(0, t - DURATION_MS + 500) / 500);
    ctx.translate(p.x + sway, p.y);
    ctx.fillStyle = p.color;
    ctx.beginPath();
    ctx.ellipse(0, 0, p.size * 0.62, p.size, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.15)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, p.size);
    ctx.lineTo(0, p.size + 26);
    ctx.stroke();
    ctx.restore();
  }

  function drawSpark(p, t) {
    if (t < 0) return; // still waiting on its burst delay
    ctx.save();
    ctx.globalAlpha = Math.max(0, 1 - t / 900);
    ctx.fillStyle = p.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function tick(now) {
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    particles = particles.filter((p) => {
      const t = now - p.born;
      if (p.kind === 'spark' && t < 0) { drawSpark(p, t); return true; }
      if (t > (p.kind === 'balloon' ? DURATION_MS + 500 : p.kind === 'spark' ? 900 : DURATION_MS)) return false;

      if (p.kind === 'confetti') {
        p.x += p.vx; p.y += p.vy; p.vy += 0.04; p.rot += p.vr;
        drawConfetti(p, t);
      } else if (p.kind === 'balloon') {
        p.y += p.vy;
        drawBalloon(p, t);
      } else if (p.kind === 'spark') {
        p.x += p.vx; p.y += p.vy; p.vy += 0.05; p.vx *= 0.98; p.vy *= 0.98;
        drawSpark(p, t);
      }
      return true;
    });

    if (particles.length) {
      rafId = requestAnimationFrame(tick);
    } else {
      rafId = null;
      if (canvas) canvas.style.display = 'none';
    }
  }

  function runCanvasEffect(spawnFn) {
    ensureCanvas();
    canvas.style.display = 'block';
    spawnFn();
    if (!rafId) rafId = requestAnimationFrame(tick);
  }

  function playBubbleEffect(className, bubbleRowEl) {
    if (!bubbleRowEl) return;
    bubbleRowEl.classList.remove(className); // restart if already mid-animation
    // eslint-disable-next-line no-unused-expressions
    void bubbleRowEl.offsetWidth; // force reflow so re-adding the class replays the animation
    bubbleRowEl.classList.add(className);
    setTimeout(() => bubbleRowEl.classList.remove(className), 900);
  }

  function play(effectName, bubbleRowEl) {
    switch (effectName) {
      case 'confetti': runCanvasEffect(spawnConfetti); break;
      case 'balloons': runCanvasEffect(spawnBalloons); break;
      case 'fireworks': runCanvasEffect(spawnFireworks); break;
      case 'slam': playBubbleEffect('fx-slam', bubbleRowEl); break;
      case 'loud': playBubbleEffect('fx-loud', bubbleRowEl); break;
      default: break;
    }
  }

  window.PingbackEffects = { play };
})();
