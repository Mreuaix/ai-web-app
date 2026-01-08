(function () {
  const canvas = document.getElementById('particles');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const state = {
    w: 0,
    h: 0,
    dpr: Math.min(2, window.devicePixelRatio || 1),
    particles: [],
    t: 0
  };

  function resize() {
    state.w = window.innerWidth;
    state.h = window.innerHeight;
    canvas.width = Math.floor(state.w * state.dpr);
    canvas.height = Math.floor(state.h * state.dpr);
    canvas.style.width = state.w + 'px';
    canvas.style.height = state.h + 'px';
    ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  }

  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  function makeParticle() {
    const speed = rand(0.25, 0.85);
    const angle = rand(0, Math.PI * 2);
    return {
      x: rand(0, state.w),
      y: rand(0, state.h),
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      r: rand(1.0, 2.2),
      a: rand(0.25, 0.7),
      hue: rand(190, 215)
    };
  }

  function init() {
    resize();
    const count = Math.max(50, Math.floor((state.w * state.h) / 18000));
    state.particles = [];
    for (let i = 0; i < count; i++) state.particles.push(makeParticle());
  }

  function step() {
    state.t += 1;
    ctx.clearRect(0, 0, state.w, state.h);

    const grad = ctx.createRadialGradient(state.w * 0.2, state.h * 0.15, 20, state.w * 0.2, state.h * 0.15, Math.max(state.w, state.h));
    grad.addColorStop(0, 'rgba(59,232,255,0.10)');
    grad.addColorStop(0.55, 'rgba(124,77,255,0.05)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, state.w, state.h);

    for (const p of state.particles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < -20) p.x = state.w + 20;
      if (p.x > state.w + 20) p.x = -20;
      if (p.y < -20) p.y = state.h + 20;
      if (p.y > state.h + 20) p.y = -20;
    }

    for (let i = 0; i < state.particles.length; i++) {
      const a = state.particles[i];
      for (let j = i + 1; j < state.particles.length; j++) {
        const b = state.particles[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy;
        const max = 130;
        if (dist2 < max * max) {
          const d = Math.sqrt(dist2);
          const alpha = (1 - d / max) * 0.22;
          ctx.strokeStyle = `rgba(59,232,255,${alpha})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    for (const p of state.particles) {
      ctx.fillStyle = `hsla(${p.hue}, 90%, 65%, ${p.a})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }

    requestAnimationFrame(step);
  }

  window.addEventListener('resize', () => {
    init();
  });

  init();
  requestAnimationFrame(step);
})();

