/**
 * PitchSelector — vanilla JS SVG interactive football pitch position selector.
 *
 * Ported from pitch.jsx (authoritative coordinate and node source).
 * No external dependencies. Self-contained CSS injected on first instantiation.
 *
 * UX parity with pitch.jsx:
 *   - Desktop landscape SVG (W=720, H=468): GK left, ST right
 *   - Mobile portrait SVG (W=420, H=740):   GK bottom, ST top
 *   - Orientation hint in header (GK ← · ST →  /  GK ↓ · ST ↑)
 *   - Selection counter (0 / 4)
 *   - Reset button (shown when any selection active)
 *   - Primary info panel (★ Primary label + position name)
 *   - Secondary chips panel (+1 · +2 · +3)
 *   - Primary ring-pulse SVG animation
 *   - Secondary +N badge animation
 *   - ST1/ST2 dual-node: both highlight when "striker" is active
 *   - Role-coloured nodes: goalkeeper/defender/midfielder/forward
 *
 * API:
 *   const ps = new PitchSelector('mount-id', (primary, all) => { ... });
 *   ps.getPrimary()  → string|null   canonical DB value of primary selection
 *   ps.getAll()      → string[]      [primary, ...secondaries]
 *   ps.reset()       → void
 */
(function (global) {
  'use strict';

  // ── Position node list (authoritative — from pitch.jsx) ─────────────────────
  // canonical = snake_case DB value.  ST1/ST2 share canonical "striker".
  var PITCH_NODES = [
    { id:'GK',  label:'GK',  name:'Goalkeeper',             role:'goalkeeper', canonical:'goalkeeper',             x:0.02, y:0.50 },
    { id:'SK',  label:'SK',  name:'Sweeper Keeper',         role:'goalkeeper', canonical:'sweeper_keeper',         x:0.10, y:0.50 },
    { id:'LB',  label:'LB',  name:'Left Back',              role:'defender',   canonical:'left_back',              x:0.19, y:0.15 },
    { id:'LCB', label:'LCB', name:'Left Centre-Back',       role:'defender',   canonical:'left_centre_back',       x:0.19, y:0.37 },
    { id:'RCB', label:'RCB', name:'Right Centre-Back',      role:'defender',   canonical:'right_centre_back',      x:0.19, y:0.63 },
    { id:'RB',  label:'RB',  name:'Right Back',             role:'defender',   canonical:'right_back',             x:0.19, y:0.85 },
    { id:'LWB', label:'LWB', name:'Left Wing-Back',         role:'defender',   canonical:'left_wing_back',         x:0.28, y:0.10 },
    { id:'RWB', label:'RWB', name:'Right Wing-Back',        role:'defender',   canonical:'right_wing_back',        x:0.28, y:0.90 },
    { id:'DM',  label:'DM',  name:'Defensive Mid',          role:'midfielder', canonical:'defensive_midfield',     x:0.37, y:0.50 },
    { id:'LCM', label:'LCM', name:'Left Centre Mid',        role:'midfielder', canonical:'left_centre_midfield',   x:0.47, y:0.33 },
    { id:'CM',  label:'CM',  name:'Centre Mid',             role:'midfielder', canonical:'centre_midfield',        x:0.50, y:0.50 },
    { id:'RCM', label:'RCM', name:'Right Centre Mid',       role:'midfielder', canonical:'right_centre_midfield',  x:0.47, y:0.67 },
    { id:'LM',  label:'LM',  name:'Left Midfielder',        role:'midfielder', canonical:'left_midfield',          x:0.55, y:0.17 },
    { id:'RM',  label:'RM',  name:'Right Midfielder',       role:'midfielder', canonical:'right_midfield',         x:0.55, y:0.83 },
    { id:'AM',  label:'AM',  name:'Att. Midfielder',        role:'midfielder', canonical:'attacking_midfield',     x:0.68, y:0.50 },
    { id:'LW',  label:'LW',  name:'Left Winger',            role:'forward',    canonical:'left_wing',              x:0.73, y:0.07 },
    { id:'RW',  label:'RW',  name:'Right Winger',           role:'forward',    canonical:'right_wing',             x:0.73, y:0.93 },
    { id:'CF',  label:'CF',  name:'Centre Forward',         role:'forward',    canonical:'centre_forward',         x:0.83, y:0.50 },
    { id:'ST1', label:'ST',  name:'Striker',                role:'forward',    canonical:'striker',                x:0.88, y:0.34 },
    { id:'ST2', label:'ST',  name:'Striker',                role:'forward',    canonical:'striker',                x:0.88, y:0.66 },
  ];

  var ROLE_COLORS = {
    goalkeeper: '#1e3a8a',
    defender:   '#1d4ed8',
    midfielder: '#15803d',
    forward:    '#b91c1c',
  };

  // SVG canvas dimensions (pitch.jsx authoritative)
  var DW = 720, DH = 468;   // desktop landscape
  var MW = 420, MH = 740;   // mobile portrait

  var MAX_SECONDARY = 3;
  var MAX_TOTAL     = 4;    // 1 primary + 3 secondary
  var MOBILE_BP     = 600;  // px — breakpoint for mobile layout

  // ── CSS injection (once per page) ───────────────────────────────────────────
  var _cssInjected = false;
  function _injectCSS() {
    if (_cssInjected) return;
    _cssInjected = true;
    var style = document.createElement('style');
    style.textContent = [
      /* Root */
      '.ps-root { font-family: inherit; }',

      /* Header bar */
      '.ps-header {',
      '  display: flex; align-items: center; gap: 0.45rem;',
      '  margin-bottom: 0.7rem; flex-wrap: wrap;',
      '}',
      '.ps-title {',
      '  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;',
      '  letter-spacing: 0.07em; color: var(--onboarding-text-muted, #6B7280);',
      '  flex-shrink: 0;',
      '}',
      '.ps-hint {',
      '  font-size: 0.72rem; color: var(--onboarding-text-muted, #6B7280);',
      '  background: var(--onboarding-option-bg, #fff);',
      '  border: 1px solid var(--onboarding-option-border, #D1D5DB);',
      '  border-radius: 4px; padding: 0.12rem 0.4rem; flex-shrink: 0;',
      '}',
      '.ps-counter {',
      '  font-size: 0.75rem; font-weight: 700;',
      '  color: var(--onboarding-text-primary, #111);',
      '  background: var(--onboarding-summary-bg, #FFFBE6);',
      '  border: 1px solid var(--onboarding-summary-border, rgba(230,189,0,0.45));',
      '  border-radius: 12px; padding: 0.12rem 0.55rem;',
      '  min-width: 40px; text-align: center; flex-shrink: 0;',
      '}',
      '.ps-reset {',
      '  margin-left: auto; font-size: 0.72rem; font-weight: 700;',
      '  color: var(--onboarding-danger-text, #C0392B);',
      '  background: var(--onboarding-danger-bg, #FFF0F0);',
      '  border: 1px solid var(--onboarding-danger-border, #E74C3C);',
      '  border-radius: 6px; padding: 0.18rem 0.65rem;',
      '  cursor: pointer; transition: background 0.15s; flex-shrink: 0;',
      '}',
      '.ps-reset:hover { background: var(--onboarding-danger-hover-bg, #FFE0E0); }',

      /* SVG container */
      '.ps-svg-wrap {',
      '  width: 100%; border-radius: 10px; overflow: hidden;',
      '  touch-action: manipulation; user-select: none;',
      '}',
      '.ps-svg { display: block; width: 100%; height: auto; }',

      /* Node interaction states */
      '.ps-node { cursor: pointer; }',
      '.ps-node--disabled { cursor: not-allowed; pointer-events: none; opacity: 0.28; }',

      /* Info panel */
      '.ps-info-panel {',
      '  margin-top: 0.6rem;',
      '  border: 1px solid var(--onboarding-summary-border, rgba(230,189,0,0.45));',
      '  border-radius: 10px; padding: 0.65rem 1rem;',
      '  background: var(--onboarding-summary-bg, #FFFBE6); min-height: 2.8rem;',
      '}',
      '.ps-primary-label {',
      '  font-size: 0.72rem; font-weight: 700; text-transform: uppercase;',
      '  letter-spacing: 0.07em; color: var(--onboarding-summary-avg, #6B5A00);',
      '  margin-bottom: 0.28rem;',
      '}',
      '.ps-primary-value {',
      '  font-size: 0.97rem; font-weight: 800;',
      '  color: var(--onboarding-text-primary, #111);',
      '}',
      '.ps-secondary-row { display: flex; flex-wrap: wrap; gap: 0.38rem; margin-top: 0.45rem; }',
      '.ps-chip {',
      '  font-size: 0.7rem; font-weight: 700; border-radius: 20px;',
      '  padding: 0.18rem 0.6rem;',
      '  background: var(--onboarding-option-hover-bg, #FFFBE6);',
      '  border: 1px solid var(--onboarding-option-hover-border, #FFD200);',
      '  color: var(--onboarding-text-primary, #111);',
      '}',
      '.ps-empty-hint {',
      '  font-size: 0.82rem; color: var(--onboarding-text-muted, #6B7280); font-style: italic;',
      '}',
    ].join('\n');
    document.head.appendChild(style);
  }

  // ── Constructor ──────────────────────────────────────────────────────────────
  function PitchSelector(mountId, onChange) {
    this._mount    = document.getElementById(mountId);
    this._onChange = typeof onChange === 'function' ? onChange : function () {};
    this._primary      = null;
    this._secondaries  = [];
    this._mobile       = false;

    _injectCSS();
    this._build();

    var self = this;
    if (typeof ResizeObserver !== 'undefined') {
      this._ro = new ResizeObserver(function () { self._checkLayout(); });
      this._ro.observe(this._mount);
    }
    this._checkLayout();
  }

  // ── Public API ───────────────────────────────────────────────────────────────
  PitchSelector.prototype.getPrimary = function () { return this._primary; };

  PitchSelector.prototype.getAll = function () {
    if (!this._primary) return [];
    return [this._primary].concat(this._secondaries);
  };

  PitchSelector.prototype.reset = function () {
    this._primary     = null;
    this._secondaries = [];
    this._render();
    this._onChange(null, []);
  };

  /**
   * Pre-populate the selector with an existing positions array.
   * all[0] is treated as primary; all[1..3] as secondaries (max MAX_TOTAL total).
   * Fires onChange so that any bound hidden inputs are updated immediately.
   */
  PitchSelector.prototype.setPositions = function (all) {
    if (!Array.isArray(all) || all.length === 0) return;
    this._primary     = all[0] || null;
    this._secondaries = all.slice(1, MAX_TOTAL);
    this._render();
    this._onChange(this._primary, this.getAll());
  };

  // ── DOM construction (once) ──────────────────────────────────────────────────
  PitchSelector.prototype._build = function () {
    this._root = document.createElement('div');
    this._root.className = 'ps-root';

    // Header
    this._headerEl  = document.createElement('div');
    this._headerEl.className = 'ps-header';

    this._titleEl   = document.createElement('span');
    this._titleEl.className = 'ps-title';
    this._titleEl.textContent = 'Select position';

    this._hintEl    = document.createElement('span');
    this._hintEl.className = 'ps-hint';
    this._hintEl.textContent = 'GK ← · ST →';

    this._counterEl = document.createElement('span');
    this._counterEl.className = 'ps-counter';
    this._counterEl.textContent = '0 / 4';

    this._resetBtn  = document.createElement('button');
    this._resetBtn.type = 'button';
    this._resetBtn.className = 'ps-reset';
    this._resetBtn.textContent = '✕ Reset';
    this._resetBtn.style.display = 'none';
    var self = this;
    this._resetBtn.addEventListener('click', function () { self.reset(); });

    this._headerEl.appendChild(this._titleEl);
    this._headerEl.appendChild(this._hintEl);
    this._headerEl.appendChild(this._counterEl);
    this._headerEl.appendChild(this._resetBtn);

    // SVG wrapper
    this._svgWrap = document.createElement('div');
    this._svgWrap.className = 'ps-svg-wrap';

    // Info panel
    this._infoPanel = document.createElement('div');
    this._infoPanel.className = 'ps-info-panel';

    this._root.appendChild(this._headerEl);
    this._root.appendChild(this._svgWrap);
    this._root.appendChild(this._infoPanel);
    this._mount.appendChild(this._root);
  };

  // ── Layout detection ─────────────────────────────────────────────────────────
  PitchSelector.prototype._checkLayout = function () {
    var wasMobile = this._mobile;
    this._mobile  = this._mount.offsetWidth < MOBILE_BP;
    if (wasMobile !== this._mobile || !this._svgWrap.firstChild) this._render();
  };

  // ── Selection logic ──────────────────────────────────────────────────────────
  PitchSelector.prototype._handleClick = function (canonical) {
    if (canonical === this._primary) {
      // Clicking the active primary → full reset
      this._primary     = null;
      this._secondaries = [];
    } else if (this._secondaries.indexOf(canonical) !== -1) {
      // Clicking a secondary → deselect it
      this._secondaries = this._secondaries.filter(function (v) { return v !== canonical; });
    } else if (!this._primary) {
      // No primary yet → set as primary
      this._primary = canonical;
    } else if (this._secondaries.length < MAX_SECONDARY) {
      // Under limit → add as secondary
      this._secondaries.push(canonical);
    }
    // At limit, unselected → ignore click

    this._render();
    this._onChange(this._primary, this.getAll());
  };

  // ── State helpers ────────────────────────────────────────────────────────────
  PitchSelector.prototype._nodeState = function (node) {
    var canon      = node.canonical;
    var total      = this.getAll().length;
    var isPrimary  = (canon === this._primary);
    var secIdx     = this._secondaries.indexOf(canon);
    var isSecondary = (secIdx !== -1);
    var isDisabled  = !isPrimary && !isSecondary && (total >= MAX_TOTAL);
    return { isPrimary: isPrimary, isSecondary: isSecondary, isDisabled: isDisabled, secIdx: secIdx };
  };

  // ── SVG builder ──────────────────────────────────────────────────────────────
  var NS = 'http://www.w3.org/2000/svg';

  function _el(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  function _lineStyle(el) {
    el.setAttribute('fill', 'none');
    el.setAttribute('stroke', 'rgba(255,255,255,0.32)');
    el.setAttribute('stroke-width', '1.5');
  }

  PitchSelector.prototype._buildSVG = function () {
    var mob  = this._mobile;
    var W    = mob ? MW : DW;
    var H    = mob ? MH : DH;
    var self = this;

    // Coordinate mapping from pitch.jsx
    var nodeX = function (n) { return mob ? (n.y * MW)       : (n.x * DW); };
    var nodeY = function (n) { return mob ? ((1 - n.x) * MH) : (n.y * DH); };

    var svg = _el('svg', { viewBox: '0 0 ' + W + ' ' + H, 'class': 'ps-svg' });

    // ── Pitch background ──
    svg.appendChild(_el('rect', { x:0, y:0, width:W, height:H, fill:'#2d5a27' }));

    // ── Alternate stripe bands (visual depth) ──
    var bandW = mob ? MH / 8 : DW / 8;
    for (var bi = 0; bi < 8; bi++) {
      if (bi % 2 === 1) {
        var band = _el('rect', {
          fill: 'rgba(0,0,0,0.06)',
        });
        if (!mob) {
          band.setAttribute('x',      bi * bandW);
          band.setAttribute('y',      0);
          band.setAttribute('width',  bandW);
          band.setAttribute('height', DH);
        } else {
          band.setAttribute('x',      0);
          band.setAttribute('y',      bi * bandW);
          band.setAttribute('width',  MW);
          band.setAttribute('height', bandW);
        }
        svg.appendChild(band);
      }
    }

    // ── Pitch markings ──
    var pad = 12;

    // Outer border
    var border = _el('rect', { x: pad, y: pad, width: W - 2*pad, height: H - 2*pad });
    _lineStyle(border); svg.appendChild(border);

    if (!mob) {
      // Desktop: centre line (vertical), centre circle, penalty areas
      var cl = _el('line', { x1: W/2, y1: pad, x2: W/2, y2: H - pad });
      _lineStyle(cl); svg.appendChild(cl);

      var cc = _el('circle', { cx: W/2, cy: H/2, r: 56 });
      _lineStyle(cc); svg.appendChild(cc);

      var hd = _el('circle', { cx: W/2, cy: H/2, r: 3, fill: 'rgba(255,255,255,0.45)' });
      svg.appendChild(hd);

      // Left penalty area (GK side)
      var lpa = _el('rect', { x: pad, y: H/2 - 88, width: 148, height: 176 });
      _lineStyle(lpa); svg.appendChild(lpa);

      var lgb = _el('rect', { x: pad, y: H/2 - 38, width: 46, height: 76 });
      _lineStyle(lgb); svg.appendChild(lgb);

      // Right penalty area (ST side)
      var rpa = _el('rect', { x: W - pad - 148, y: H/2 - 88, width: 148, height: 176 });
      _lineStyle(rpa); svg.appendChild(rpa);

      var rgb_ = _el('rect', { x: W - pad - 46, y: H/2 - 38, width: 46, height: 76 });
      _lineStyle(rgb_); svg.appendChild(rgb_);

    } else {
      // Mobile: centre line (horizontal), centre circle, penalty areas
      var mcl = _el('line', { x1: pad, y1: H/2, x2: W - pad, y2: H/2 });
      _lineStyle(mcl); svg.appendChild(mcl);

      var mcc = _el('circle', { cx: W/2, cy: H/2, r: 56 });
      _lineStyle(mcc); svg.appendChild(mcc);

      var mhd = _el('circle', { cx: W/2, cy: H/2, r: 3, fill: 'rgba(255,255,255,0.45)' });
      svg.appendChild(mhd);

      // Top penalty area (ST side)
      var tpa = _el('rect', { x: W/2 - 88, y: pad, width: 176, height: 148 });
      _lineStyle(tpa); svg.appendChild(tpa);

      var tgb = _el('rect', { x: W/2 - 38, y: pad, width: 76, height: 46 });
      _lineStyle(tgb); svg.appendChild(tgb);

      // Bottom penalty area (GK side)
      var bpa = _el('rect', { x: W/2 - 88, y: H - pad - 148, width: 176, height: 148 });
      _lineStyle(bpa); svg.appendChild(bpa);

      var bgb = _el('rect', { x: W/2 - 38, y: H - pad - 46, width: 76, height: 46 });
      _lineStyle(bgb); svg.appendChild(bgb);
    }

    // ── Position nodes ────────────────────────────────────────────────────────
    PITCH_NODES.forEach(function (node) {
      var st = self._nodeState(node);
      var cx = nodeX(node);
      var cy = nodeY(node);
      var baseColor = ROLE_COLORS[node.role] || '#374151';

      var g = _el('g', {
        'class': 'ps-node' +
          (st.isPrimary   ? ' ps-node--primary'   : '') +
          (st.isSecondary ? ' ps-node--secondary' : '') +
          (st.isDisabled  ? ' ps-node--disabled'  : ''),
        'data-canonical': node.canonical,
      });

      if (!st.isDisabled) {
        g.addEventListener('click', function () { self._handleClick(node.canonical); });
      }

      // ── Pulse ring for primary ──
      if (st.isPrimary) {
        var ring = _el('circle', { cx: cx, cy: cy, r: 13, fill: 'none',
          stroke: '#FFD200', 'stroke-width': '2', opacity: '0.65' });
        var ra = _el('animate', { attributeName: 'r',
          values: '13;20;13', dur: '1.4s', repeatCount: 'indefinite' });
        var oa = _el('animate', { attributeName: 'opacity',
          values: '0.65;0.1;0.65', dur: '1.4s', repeatCount: 'indefinite' });
        ring.appendChild(ra);
        ring.appendChild(oa);
        g.appendChild(ring);
      }

      // ── Main circle ──
      var circleAttrs = { cx: cx, cy: cy, r: '13', 'class': 'ps-node-circle' };
      if (st.isPrimary) {
        circleAttrs.fill = '#FFD200';
        circleAttrs.stroke = '#C9A400';
        circleAttrs['stroke-width'] = '2.5';
      } else if (st.isSecondary) {
        circleAttrs.fill = '#FFFBE6';
        circleAttrs.stroke = '#FFD200';
        circleAttrs['stroke-width'] = '2';
      } else {
        circleAttrs.fill = baseColor;
        circleAttrs.stroke = 'rgba(255,255,255,0.35)';
        circleAttrs['stroke-width'] = '1';
      }
      g.appendChild(_el('circle', circleAttrs));

      // ── Label ──
      var txtFill = st.isPrimary ? '#0B0B0B' : st.isSecondary ? '#6B5A00' : '#FFFFFF';
      var fsize   = node.label.length > 2 ? '6.5' : '8.5';
      var txt = _el('text', {
        x: cx, y: cy + 3.5,
        'text-anchor': 'middle',
        'font-size': fsize,
        'font-weight': '800',
        'font-family': 'inherit',
        fill: txtFill,
        'pointer-events': 'none',
      });
      txt.textContent = node.label;
      g.appendChild(txt);

      // ── Primary ★ badge ──
      if (st.isPrimary) {
        var pbg = _makeBadge(cx + 10, cy - 10, '#0B0B0B', '#FFD200', '#FFD200', '★', 8);
        g.appendChild(pbg);
      }

      // ── Secondary +N badge ──
      if (st.isSecondary) {
        var sbg = _makeBadge(cx + 10, cy - 10, '#FFD200', '#C9A400', '#0B0B0B', '+' + (st.secIdx + 1), 7);
        g.appendChild(sbg);
      }

      svg.appendChild(g);
    });

    return svg;
  };

  // Build a small circular badge with a text label, with a pop animation.
  function _makeBadge(cx, cy, fill, stroke, textFill, label, fontSize) {
    var g = _el('g');

    // animateTransform for pop — SVG-native, no CSS required
    var at = _el('animateTransform', {
      attributeName: 'transform',
      type: 'scale',
      additive: 'sum',
      values: '0 0;1.25 1.25;1 1',
      keyTimes: '0;0.55;1',
      dur: '0.25s',
      fill: 'freeze',
    });
    // The transform-origin workaround: use a nested group with translate
    var inner = _el('g', { transform: 'translate(' + cx + ',' + cy + ')' });

    var animG = _el('g');
    animG.appendChild(at);

    var bc = _el('circle', { cx: 0, cy: 0, r: 7, fill: fill, stroke: stroke, 'stroke-width': '1.2' });
    var bt = _el('text', {
      x: 0, y: fontSize === 8 ? 3 : 2.5,
      'text-anchor': 'middle',
      'font-size': fontSize,
      'font-weight': '900',
      fill: textFill,
      'pointer-events': 'none',
      'font-family': 'inherit',
    });
    bt.textContent = label;

    animG.appendChild(bc);
    animG.appendChild(bt);
    inner.appendChild(animG);
    g.appendChild(inner);
    return g;
  }

  // ── Full render ──────────────────────────────────────────────────────────────
  PitchSelector.prototype._render = function () {
    // SVG
    this._svgWrap.innerHTML = '';
    this._svgWrap.appendChild(this._buildSVG());

    // Header
    var total = this.getAll().length;
    this._counterEl.textContent = total + ' / ' + MAX_TOTAL;
    this._hintEl.textContent = this._mobile
      ? 'GK ↓ · ST ↑'
      : 'GK ← · ST →';
    this._resetBtn.style.display = this._primary ? 'inline-block' : 'none';

    // Info panel
    var html = '';
    if (!this._primary) {
      html = '<span class="ps-empty-hint">Select your primary position on the pitch</span>';
    } else {
      var pNode = null;
      for (var i = 0; i < PITCH_NODES.length; i++) {
        if (PITCH_NODES[i].canonical === this._primary) { pNode = PITCH_NODES[i]; break; }
      }
      var pLabel = pNode ? pNode.label : this._primary;
      var pName  = pNode ? pNode.name  : this._primary;

      html += '<div class="ps-primary-label">★ Primary</div>';
      html += '<div class="ps-primary-value">' + pLabel + ' — ' + pName + '</div>';

      if (this._secondaries.length > 0) {
        html += '<div class="ps-secondary-row">';
        for (var j = 0; j < this._secondaries.length; j++) {
          var sCanon = this._secondaries[j];
          var sNode  = null;
          for (var k = 0; k < PITCH_NODES.length; k++) {
            if (PITCH_NODES[k].canonical === sCanon) { sNode = PITCH_NODES[k]; break; }
          }
          var sLabel = sNode ? sNode.label : sCanon;
          var sName  = sNode ? sNode.name  : sCanon;
          html += '<span class="ps-chip">+' + (j + 1) + ' ' + sLabel + ' — ' + sName + '</span>';
        }
        html += '</div>';
      } else {
        html += '<div class="ps-secondary-row">'
          + '<span class="ps-empty-hint">Tap up to 3 secondary positions</span>'
          + '</div>';
      }
    }
    this._infoPanel.innerHTML = html;
  };

  // ── Export ───────────────────────────────────────────────────────────────────
  global.PitchSelector = PitchSelector;

}(typeof window !== 'undefined' ? window : this));
