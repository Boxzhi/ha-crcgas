/**
 * crcgas-statistics-card v4.1 — 华润燃气对比统计卡片（通用版）
 * - 双年对比折线图（SVG 绘制）
 * - 用气量/费用切换
 * - 阶梯用量进度条
 * - 本月用量及费用
 * - 年汇总
 *
 * 使用方式:
 *   type: 'custom:crcgas-statistics-card'
 *   可选配置:
 *     title: '燃气统计'（默认）
 *     entity_prefix: 'sensor.xxx'（自动检测 crcgas 域实体, 也可手动设置）
 *     history_entity: 'sensor.xxx_ran_qi_biao_li_shi_lei_ji'（历史累计传感器, 可选）
 *     balance_entity: 'sensor.xxx_ran_qi_zhang_hu_yu_e'（余额传感器, 可选）
 */
const MONTHS_SHORT = ['1','2','3','4','5','6','7','8','9','10','11','12'];

/** 计算 Y 轴步长的"好数" */
function _niceStep(maxV) {
  if (maxV < 5) return 1;
  if (maxV < 20) return 5;
  if (maxV < 50) return 10;
  if (maxV < 100) return 20;
  if (maxV < 200) return 50;
  if (maxV < 500) return 100;
  if (maxV < 1000) return 200;
  return Math.round(maxV / 20 / 100) * 100;
}

// Missing measurements are null, never an invented zero or tariff.
function _numeric(value) {
  if (value == null || typeof value === 'boolean' || String(value).trim() === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function _metric(value) {
  const n = _numeric(value);
  return n !== null && n >= 0 ? n : null;
}
function _money(value) { return value == null ? '—' : '¥' + value.toFixed(0); }
function _sumKnown(rows, key) {
  if (!rows.length || rows.some(row => _metric(row[key]) === null)) return null;
  return rows.reduce((total, row) => total + _metric(row[key]), 0);
}
function _tierProgress(ld) {
  const bars = [];
  for (const [label, limit, used, remain, cls] of [
    ['一阶', ld.step1Limit, ld.step1Sum, ld.step1Remain, ''],
    ['二阶', ld.step2Limit, ld.step2Sum, ld.step2Remain, ' s2'],
  ]) {
    if ([limit, used, remain].some(v => _metric(v) === null) ||
        limit <= 0 || used > limit || Math.abs(limit - used - remain) > 0.2) continue;
    const pct = (used / limit * 100).toFixed(1);
    bars.push(`<div class="st"><div class="sh"><span class="sl">${label}已用 ${used}/${limit} m³</span><span class="sr">余 ${remain} m³</span></div><div class="sp"><div class="sf${cls}" style="width:${pct}%"></div></div></div>`);
  }
  if (ld.step3Sum != null && ld.step3Sum > 0) {
    bars.push(`<div class="st"><div class="sh"><span class="sl">三阶已用 ${ld.step3Sum} m³</span></div></div>`);
  }
  const status = ld.tierStatus === 'stale' ? '<div class="sh">阶梯数据待更新</div>' : '';
  return `<div class="ts">${status}${bars.join('') || '阶梯数据暂无'}</div>`;
}

/** 生成 SVG 悬浮提示（在图表内部渲染） */
function _makeTipSVG(month, d1, d2, y1, y2, PL, SX, H, W, pos) {
  const sd1 = d1[month];
  const sd2 = d2[month];
  if (!sd1 && !sd2) return '';
  const g1 = _metric(sd1?.change) === null ? '—' : Number(sd1.change).toFixed(1);
  const g2 = _metric(sd2?.change) === null ? '—' : Number(sd2.change).toFixed(1);
  const c1 = _money(_metric(sd1?.cost));
  const c2 = _money(_metric(sd2?.cost));
  const TW = 100, TH = 44;
  let tx = pos ? Math.max(2, Math.min(W - TW - 2, pos.x - TW / 2)) : 2;
  let ty = pos ? Math.max(2, Math.min(H - TH - 2, pos.y - TH - 8)) : 2;
  if (ty < 2) ty = pos ? Math.min(H - TH - 2, pos.y + 10) : 2;
  return `<g style="pointer-events:none;opacity:0.88">
<rect x="${tx}" y="${ty}" width="${TW}" height="${TH}" rx="4" fill="var(--card-background-color)" stroke="var(--divider-color)" stroke-width="0.5"/>
<text x="${tx+6}" y="${ty+13}" fill="var(--primary-text-color)" font-size="10" font-weight="600">${month+1}月</text>
<text x="${tx+6}" y="${ty+26}" fill="#ff7043" font-size="9">● 本年 ${g1}m³ ${c1}</text>
<text x="${tx+6}" y="${ty+38}" fill="#7c4dff" font-size="9">● 去年 ${g2}m³ ${c2}</text>
</g>`;
}

/** 根据 prefix 生成 crcgas 常见传感器 ID 列表 */
function _buildEntityIds(prefix) {
  const p = prefix.replace(/[_\s]+$/, '');
  return {
    balance: `${p}_ran_qi_zhang_hu_yu_e`,
    monthlyUsage: `${p}_ben_yue_lei_ji_yong_qi_liang`,
    monthlyCost: `${p}_zhang_dan_jin_e`,
    step1Remain: [`${p}_yi_jie_ti_sheng_yu_qi_liang`, `${p}_yi_dang_sheng_yu_qi_liang`],
    step2Remain: [`${p}_er_jie_ti_sheng_yu_qi_liang`, `${p}_er_dang_sheng_yu_qi_liang`],
    status: `${p}_ji_cheng_zhuang_tai`,
    tierStatus: `${p}_jie_ti_shu_ju_zhuang_tai`,
    step1Limit: `${p}_yi_dang_zong_xian_e`,
    step1Sum: [`${p}_yi_jie_ti_lei_ji_yi_yong`, `${p}_yi_dang_lei_ji_yong_liang`],
    step2Sum: [`${p}_er_jie_ti_lei_ji_yi_yong`, `${p}_er_dang_lei_ji_yong_liang`],
    step3Sum: `${p}_san_jie_ti_lei_ji_yi_yong`,
    step2Limit: `${p}_er_dang_zong_xian_e`,
    latestUsage: `${p}_ben_qi_yong_qi_liang`,
    latestBill: `${p}_zhang_dan_jin_e`,
    latestPeriod: `${p}_ben_qi_chao_biao_shi_jian`,
  };
}

/** 扫描 hass.states, 自动找出 crcgas 域的实体前缀 */
function _autoDetectPrefix(states) {
  // 找 entity_id 最长的公共前缀（去掉末尾的传感器名称部分）
  const crcgasEntities = Object.keys(states).filter(id => id.startsWith('sensor.') && id.includes('ran_qi'));
  if (crcgasEntities.length === 0) return null;

  // 优先找 "余额" 传感器 → 提取前缀
  const balanceEntity = crcgasEntities.find(id => id.endsWith('ran_qi_zhang_hu_yu_e'));
  if (balanceEntity) {
    return balanceEntity.replace(/_ran_qi_zhang_hu_yu_e$/, '');
  }

  // 兜底: 找用气量传感器
  const usageEntity = crcgasEntities.find(id => id.endsWith('ben_yue_lei_ji_yong_qi_liang'));
  if (usageEntity) {
    return usageEntity.replace(/_ben_yue_lei_ji_yong_qi_liang$/, '');
  }

  // 最后兜底: 取第一个（截掉末尾最有可能是独立名称的部分）
  const first = crcgasEntities[0];
  const parts = first.split('_');
  if (parts.length > 4) {
    return parts.slice(0, -4).join('_');
  }
  return null;
}

class CrcgasStatisticsCard extends HTMLElement {
  setConfig(config) {
    if (!config) throw new Error('Invalid configuration');

    // 重新初始化（HA 编辑卡片配置时会再次调用 setConfig）
    this._yearData = {};
    this._loaded = false;

    // 优先使用用户配置的 entity_prefix
    this._userPrefix = config.entity_prefix || null;
    this._config = {
      title: config.title || '🔥 燃气统计',
      historyEntity: config.history_entity || null,
      balanceEntity: config.balance_entity || null,
    };
    this._year = new Date().getFullYear();
    this._viewMode = 'gas';
    this._chartType = 'line';
    this._cardId = 'crcgas-v3-' + Math.random().toString(36).substr(2, 9);
    this._yearData = {};
    this._liveData = {
      balance: null, monthlyUsage: null, monthlyCost: null,
      step1Remain: null, step2Remain: null, status: null,
      tierStatus: null, step2Sum: null, step3Sum: null,
      step1Limit: null, step2Limit: null, step1Sum: null,
      latestUsage: null, latestBill: null, latestPeriod: null,
    };
    this._loading = false;
    this._loaded = false;
    this._autoDetected = false;
    this._selectedMonth = null; // 点击锁定月份
    this._hoverMonth = null; // 鼠标悬停月份
    this._hoverPos = null; // 鼠标位置 {x, y}
    this._render();
    if (this._hass) this._loadData();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) this._loadData();
    else this._debouncedLoad();
  }

  connectedCallback() {
    if (this._hass && !this._loaded) this._loadData();
    if (!this._boundClick) {
      this._boundClick = (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.dataset.action;
        if (action === 'sw') { this._viewMode = btn.dataset.mode; this._selectedMonth = null; this._render(); }
        else if (action === 'ct') { this._chartType = btn.dataset.ct; this._selectedMonth = null; this._render(); }
        else if (action === 'cy') this._cy(Number(btn.dataset.dir));
        else if (action === 'month') {
          const y = Number(btn.dataset.year);
          const m = Number(btn.dataset.month);
          this._selectedMonth = { year: y, month: m };
          this._render();
        }
      };
      this.addEventListener('click', this._boundClick);

      this._boundHover = (e) => {
        const btn = e.target.closest('[data-action="month"]');
        if (btn && e.type === 'mouseover') {
          const y = Number(btn.dataset.year);
          const m = Number(btn.dataset.month);
          if (!this._hoverMonth || this._hoverMonth.month !== m) {
            this._hoverMonth = { year: y, month: m };
            if (!this._hoverRaf) {
              this._hoverRaf = requestAnimationFrame(() => {
                this._hoverRaf = null;
                this._render();
              });
            }
          }
        } else if (!btn && e.type === 'mouseout') {
          // 只在真的离开图表区域时才清除
          const chartEl = e.currentTarget.querySelector('.ca svg');
          if (chartEl && !chartEl.contains(e.relatedTarget)) {
            this._hoverMonth = null;
            this._selectedMonth = null;
            if (!this._hoverRaf) {
              this._hoverRaf = requestAnimationFrame(() => {
                this._hoverRaf = null;
                this._render();
              });
            }
          }
        }
      };
      this.addEventListener('mouseover', this._boundHover);
      this.addEventListener('mouseout', this._boundHover);
    }
    // mousemove 追踪鼠标位置（独立监听，不触发重绘）
    if (!this._boundMove) {
      this._boundMove = (e) => {
        const svg = e.target.closest('svg');
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        this._hoverPos = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      };
      this.addEventListener('mousemove', this._boundMove);
    }
  }

  disconnectedCallback() {
    if (this._boundClick) {
      this.removeEventListener('click', this._boundClick);
      this._boundClick = null;
    }
    if (this._boundHover) {
      this.removeEventListener('mouseover', this._boundHover);
      this.removeEventListener('mouseout', this._boundHover);
      this._boundHover = null;
    }
    if (this._boundMove) {
      this.removeEventListener('mousemove', this._boundMove);
      this._boundMove = null;
    }
  }

  /** 防抖：批量状态变更仅触发一次渲染 */
  _debouncedLoad() {
    if (this._loadTimer) cancelAnimationFrame(this._loadTimer);
    this._loadTimer = requestAnimationFrame(() => {
      this._loadTimer = null;
      this._loadCurrentState();
    });
  }

  /** 自动检测 entity_prefix */
  _ensurePrefix() {
    if (this._entityPrefix) return;
    if (this._userPrefix) {
      this._entityPrefix = this._userPrefix;
      return;
    }
    if (!this._hass || !this._hass.states) return;
    const detected = _autoDetectPrefix(this._hass.states);
    if (detected) {
      this._entityPrefix = detected;
      this._autoDetected = true;
      console.log(`crcgas-card: auto-detected entity_prefix = ${detected}`);
    } else {
      // 完全兜底: 用 v3.1 的旧默认值
      this._entityPrefix = 'sensor.chu_fang_hua_run_ran_qi';
      console.warn('crcgas-card: 未能自动检测，使用旧默认值', this._entityPrefix);
    }
  }

  async _loadData() {
    if (!this._hass || this._loaded) return;
    this._ensurePrefix();
    this._loaded = true;
    this._loading = true;
    this._loadCurrentState();
    try {
      if (!this._yearData[this._year]) await this._loadYear(this._year);
      if (!this._yearData[this._year - 1]) await this._loadYear(this._year - 1);
    } catch (e) {
      console.error('crcgas: load error', e);
    }
    this._loading = false;
    this._render();
  }

  async _loadYear(year) {
    if (this._yearData[year]) return;
    const start = new Date(year, 0, 1).toISOString();
    const end = new Date(year + 1, 0, 1).toISOString();
    try {
      // 使用外部统计（完整历史数据）
      const statIds = ['crcgas:monthly_gas_usage', 'crcgas:monthly_bill_amount'];
      // 用户配置的 historyEntity 可覆盖用气统计
      if (this._config.historyEntity) statIds[0] = this._config.historyEntity;
      const result = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: start,
        end_time: end,
        statistic_ids: statIds,
        period: 'month',
      });
      const gasStats = result?.[statIds[0]] || [];
      const costStats = result?.[statIds[1]] || [];
      // HA month statistics expose real measured state/change; missing amounts stay null.
      const byMonth = {};
      const monthOf = value => {
        if (value == null) return null;
        const d = new Date(value);
        if (!Number.isFinite(d.getTime())) return null;
        const parts = new Intl.DateTimeFormat('en-US', {
          timeZone: this._hass.config?.time_zone || 'Asia/Shanghai',
          year: 'numeric', month: 'numeric'
        }).formatToParts(d);
        if (Number(parts.find(p => p.type === 'year')?.value) !== year) return null;
        return Number(parts.find(p => p.type === 'month')?.value) - 1;
      };
      for (const [rows, key] of [[gasStats, 'change'], [costStats, 'cost']]) {
        for (const row of rows) {
          const m = monthOf(row.start);
          if (m === null) continue;
          if (!byMonth[m]) byMonth[m] = {change: null, cost: null};
          const raw = key === 'cost' || !this._config.historyEntity
            ? (_metric(row.state) ?? _metric(row.change)) : _metric(row.change);
          byMonth[m][key] = raw;
        }
      }
      this._yearData[year] = byMonth;
    } catch (e) {
      this._yearData[year] = {};
    }
  }

  _loadCurrentState() {
    if (!this._hass || !this._entityPrefix) return;
    const ids = _buildEntityIds(this._entityPrefix);
    const states = this._hass.states;
    const _val = (ids) => {
      for (const id of Array.isArray(ids) ? ids : [ids]) {
        // An existing but unavailable entity must not silently borrow another account's value.
        if (states[id]) return _numeric(states[id].state);
      }
      return null;
    };
    const _str = (id, def = null) => states[id] ? states[id].state : def;

    this._liveData = {
      balance: this._config.balanceEntity
        ? _val(this._config.balanceEntity)
        : _val(ids.balance),
      monthlyUsage: _val(ids.monthlyUsage),
      monthlyCost: _val(ids.monthlyCost),
      step1Remain: _val(ids.step1Remain),
      step2Remain: _val(ids.step2Remain),
      status: _str(ids.status),
      tierStatus: _str(ids.tierStatus),
      step1Limit: _val(ids.step1Limit),
      step2Limit: _val(ids.step2Limit),
      step1Sum: _val(ids.step1Sum),
      step2Sum: _val(ids.step2Sum),
      step3Sum: _val(ids.step3Sum),
      latestUsage: _val(ids.latestUsage),
      latestBill: _val(ids.latestBill),
      latestPeriod: _str(ids.latestPeriod),
    };
    if (!this._loading) this._render();
  }

  _renderChart(y1, y2, mode, chartType, hoverMonth, hoverPos) {
    const d1 = this._yearData[y1] || {};
    const d2 = this._yearData[y2] || {};
    const months = 12;
    const v1 = [], v2 = [];
    for (let m = 0; m < months; m++) {
      const key = mode === 'cost' ? 'cost' : 'change';
      v1.push(_metric(d1[m]?.[key]));
      v2.push(_metric(d2[m]?.[key]));
    }
    const maxV = Math.max(...v1, ...v2, 1);
    const W = 280, H = 140, PT = 16, PB = 22, PL = 30, PR = 6;
    const CW = W - PL - PR, CH = H - PT - PB;
    const SX = CW / (months > 1 ? months - 1 : 1);
    const BW = months > 1 ? CW / months * 0.22 : 8; // 柱宽
    const C1 = '#ff7043', C2 = '#7c4dff';

    const py = (v) => PT + CH - (v / maxV) * CH * 0.85;

    if (chartType === 'bar') {
      // 柱状图
      let bars = '', labels = '';
      for (let i = 0; i < months; i++) {
        const cx = PL + i * SX;
        const h1 = v1[i] > 0 ? (v1[i] / maxV) * CH * 0.85 : 0;
        const h2 = v2[i] > 0 ? (v2[i] / maxV) * CH * 0.85 : 0;
        const y1b = H - PB;
        if (h1 > 0) bars += `<rect x="${(cx - BW).toFixed(1)}" y="${(y1b - h1).toFixed(1)}" width="${BW.toFixed(1)}" height="${h1.toFixed(1)}" fill="${C1}" opacity="0.85" rx="2" cursor="pointer" data-action="month" data-year="${y1}" data-month="${i}"/><rect x="${(cx - BW).toFixed(1)}" y="${PT.toFixed(1)}" width="${BW.toFixed(1)}" height="${(CH*0.85).toFixed(1)}" fill="transparent" cursor="pointer" data-action="month" data-year="${y1}" data-month="${i}"/>`;
        if (h2 > 0) bars += `<rect x="${cx.toFixed(1)}" y="${(y1b - h2).toFixed(1)}" width="${BW.toFixed(1)}" height="${h2.toFixed(1)}" fill="${C2}" opacity="0.85" rx="2" cursor="pointer" data-action="month" data-year="${y2}" data-month="${i}"/><rect x="${cx.toFixed(1)}" y="${PT.toFixed(1)}" width="${BW.toFixed(1)}" height="${(CH*0.85).toFixed(1)}" fill="transparent" cursor="pointer" data-action="month" data-year="${y2}" data-month="${i}"/>`;
        if (i % 2 === 0) labels += `<text x="${(cx).toFixed(1)}" y="${H-4}" text-anchor="middle" fill="var(--secondary-text-color)" font-size="9">${i+1}月</text>`;
      }
      let grid = '';
      const step = _niceStep(maxV);
      for (let i = 0; i < 4; i++) {
        const y = PT + (CH / 3) * i;
        const val = step * (3 - i);
        const label = val > 0 ? val.toString() : '0';
        grid += `<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W-PR}" y2="${y.toFixed(1)}" stroke="var(--divider-color)" stroke-width="0.5"/>
<text x="${PL-4}" y="${y.toFixed(1)+3}" text-anchor="end" fill="var(--secondary-text-color)" font-size="8">${label}</text>`;
      }
      const highlightCol = hoverMonth !== null
        ? '<rect x="' + (Math.max(PL, PL + hoverMonth * SX - SX * 0.45)).toFixed(1) + '" y="' + PT + '" width="' + (SX * 0.9).toFixed(1) + '" height="' + CH.toFixed(1) + '" class="crc-hover" rx="4"/>'
        : '';
      const tipSVG = hoverMonth !== null ? _makeTipSVG(hoverMonth, d1, d2, y1, y2, PL, SX, H, W, hoverPos) : '';
      return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:100%;display:block;"><style>.crc-hover{fill:var(--primary-color);opacity:0.08;pointer-events:none}</style>${grid}${highlightCol}${bars}${labels}${tipSVG}</svg>`;
    }

    // 折线图
    const lineSVG = (vals, color, year) => {
      let pts = '', dots = '', area = '', segment = [];
      const closeSegment = () => {
        if (!segment.length) return;
        const first = segment[0], last = segment[segment.length - 1];
        area += first.path + segment.slice(1).map(p => p.path.replace(/^M/, 'L')).join('') + ` L${last.x},${H-PB} L${first.x},${H-PB} Z`;
        segment = [];
      };
      for (let i = 0; i < months; i++) {
        if (vals[i] === null) { closeSegment(); continue; }
        const x = PL + i * SX, y = Math.max(PT, Math.min(H - PB, py(vals[i])));
        const point = x.toFixed(1) + ',' + y.toFixed(1);
        pts += (segment.length ? 'L' : 'M') + point;
        segment.push({x: x.toFixed(1), path: 'M' + point});
        if (vals[i] !== null) dots += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" fill="${color}" opacity="0.9" cursor="pointer" data-action="month" data-year="${year}" data-month="${i}"/><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="14" fill="transparent" cursor="pointer" data-action="month" data-year="${year}" data-month="${i}"/>`;
      }
      closeSegment();
      return `<path d="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
<path d="${area}" fill="${color}" opacity="0.08"/>
${dots}`;
    };

    let grid = '';
    const step = _niceStep(maxV);
    for (let i = 0; i < 4; i++) {
        const y = PT + (CH / 3) * i;
        const val = step * (3 - i);
        const label = val > 0 ? val.toString() : '0';
      grid += `<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W-PR}" y2="${y.toFixed(1)}" stroke="var(--divider-color)" stroke-width="0.5"/>
<text x="${PL-4}" y="${y.toFixed(1)+3}" text-anchor="end" fill="var(--secondary-text-color)" font-size="8">${label}</text>`;
    }
    let labels = '';
    for (let i = 0; i < months; i += 2) {
      labels += `<text x="${(PL + i * SX).toFixed(1)}" y="${H-4}" text-anchor="middle" fill="var(--secondary-text-color)" font-size="9">${i+1}月</text>`;
    }
    const highlightCol = hoverMonth !== null
      ? '<rect x="' + (Math.max(PL, PL + hoverMonth * SX - SX * 0.45)).toFixed(1) + '" y="' + PT + '" width="' + (SX * 0.9).toFixed(1) + '" height="' + CH.toFixed(1) + '" class="crc-hover" rx="4"/>'
      : '';
    const tipSVG = hoverMonth !== null ? _makeTipSVG(hoverMonth, d1, d2, y1, y2, PL, SX, H, W, hoverPos) : '';
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:100%;display:block;pointer-events:auto;"><style>.crc-hover{fill:var(--primary-color);opacity:0.08;pointer-events:none}</style>${grid}${highlightCol}${lineSVG(v1, C1, y1)}${lineSVG(v2, C2, y2)}${labels}${tipSVG}</svg>`;
  }

  _render() {
    const y1 = this._year, y2 = this._year - 1, mode = this._viewMode;
    const d1 = this._yearData[y1] || {}, d2 = this._yearData[y2] || {};
    const ld = this._liveData || {};
    const maxMonth = new Date().getMonth();
    const periodRows = d => Object.entries(d).filter(([m]) => Number(m) <= maxMonth).map(([,v]) => v);
    const y1Total = _sumKnown(periodRows(d1), 'change');
    const y2Total = _sumKnown(periodRows(d2), 'change');
    const y1Cost = _sumKnown(periodRows(d1), 'cost');
    const y2Cost = _sumKnown(periodRows(d2), 'cost');
    const unit = mode === 'gas' ? 'm³' : '元';
    const unitLabel = mode === 'gas' ? '用气量' : '费用';
    const y1Val = mode === 'gas' ? y1Total?.toFixed(1) ?? '—' : _money(y1Cost);
    const y2Val = mode === 'gas' ? y2Total?.toFixed(1) ?? '—' : _money(y2Cost);
    const first = mode === 'gas' ? y1Total : y1Cost;
    const second = mode === 'gas' ? y2Total : y2Cost;
    const diff = first === null || second === null ? null : first - second;
    const diffColor = diff > 0 ? '#f44336' : diff < 0 ? '#4caf50' : 'var(--secondary-text-color)';
    const diffSym = diff > 0 ? '↑' : diff < 0 ? '↓' : '→';
    // 从统计数据中找最近有数据的月份（确保和图表的月份一致）
    const _latestDataMonth = Object.keys(d1).filter(m => Number(m) <= maxMonth && (d1[m]?.change || 0) > 0).map(Number).sort((a,b)=>b-a)[0];
    const periodLabel = _latestDataMonth !== undefined ? (_latestDataMonth+1)+'月' : (ld.latestPeriod||'最近一期');

    this.innerHTML = `
<style>#${this._cardId}{font-family:var(--paper-font-body1_-_font-family)}#${this._cardId} ha-card{border-radius:12px;overflow:hidden}#${this._cardId} .b{padding:14px}#${this._cardId} .h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}#${this._cardId} .ht{font-size:16px;font-weight:600;color:var(--primary-text-color)}#${this._cardId} .ha{display:flex;align-items:center;gap:6px}#${this._cardId} .nb{background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:3px 8px;cursor:pointer;font-size:14px;color:var(--primary-text-color);line-height:1.4}#${this._cardId} .nb:hover{background:var(--primary-color);color:#fff}#${this._cardId} .nb.a{background:var(--primary-color);color:#fff;border-color:var(--primary-color)}#${this._cardId} .yt{font-size:14px;font-weight:500;min-width:44px;text-align:center;color:var(--primary-text-color)}#${this._cardId} .cl{display:flex;justify-content:center;gap:20px;font-size:11px;margin-bottom:4px}#${this._cardId} .li{display:flex;align-items:center;gap:4px}#${this._cardId} .ld{width:8px;height:8px;border-radius:50%}#${this._cardId} .ca{position:relative}#${this._cardId} .sr{display:flex;gap:6px;margin-bottom:10px}#${this._cardId} .sc{flex:1;background:var(--secondary-background-color);border-radius:8px;padding:8px 6px;text-align:center;min-width:0}#${this._cardId} .sv{font-size:15px;font-weight:700;color:var(--primary-text-color)}#${this._cardId} .sv.w{color:#f44336}#${this._cardId} .sl{font-size:10px;color:var(--secondary-text-color);margin-top:1px}#${this._cardId} .sd{font-size:10px;margin-top:1px}#${this._cardId} .ts{margin-bottom:10px}#${this._cardId} .st{margin-bottom:8px}#${this._cardId} .st:last-child{margin-bottom:0}#${this._cardId} .sh{display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px}#${this._cardId} .sl{color:var(--primary-text-color);font-weight:500}#${this._cardId} .sr{color:var(--secondary-text-color)}#${this._cardId} .sp{height:10px;border-radius:5px;background:var(--divider-color);overflow:hidden}#${this._cardId} .sf{height:100%;border-radius:5px;background:linear-gradient(90deg,#66bb6a,#43a047);transition:width .2s}#${this._cardId} .sf.s2{background:linear-gradient(90deg,#ffa726,#f57c00)}#${this._cardId} .f{margin-top:10px}#${this._cardId} .fr{display:flex;justify-content:space-between;padding:3px 0;font-size:13px;border-top:1px solid var(--divider-color);color:var(--primary-text-color)}#${this._cardId} .fv{font-weight:600}#${this._cardId} .ldg{text-align:center;padding:30px 0;color:var(--secondary-text-color);font-size:14px}</style>
<div id="${this._cardId}"><ha-card><div class="b">
<div class="h"><span class="ht">${this._config.title}</span>
<div class="ha"><button class="nb${mode==='gas'?' a':''}" data-action="sw" data-mode="gas">m³</button><button class="nb${mode==='cost'?' a':''}" data-action="sw" data-mode="cost">¥</button>
<span style="width:1px;height:18px;background:var(--divider-color);margin:0 2px"></span>
<button class="nb${this._chartType==='line'?' a':''}" data-action="ct" data-ct="line">📈</button><button class="nb${this._chartType==='bar'?' a':''}" data-action="ct" data-ct="bar">📊</button></div></div>
${this._loading?'<div class="ldg">加载中...</div>':''}
${!this._loading?`
<div class="ha" style="justify-content:center;margin-bottom:6px;gap:8px"><button class="nb" data-action="cy" data-dir="-1">&lsaquo;</button><span class="yt">${this._year}</span><button class="nb" data-action="cy" data-dir="1">&rsaquo;</button></div>
<div class="cl"><span class="li"><span class="ld" style="background:#ff7043"></span> 本年</span><span class="li"><span class="ld" style="background:#7c4dff"></span> 去年同期</span></div>
<div class="ca">${this._renderChart(y1,y2,mode,this._chartType,this._hoverMonth?this._hoverMonth.month:null,this._hoverPos)}</div>
<div class="sr">
<div class="sc"><div class="sv">${y1Val}</div><div class="sl">本年${unitLabel}</div><div class="sd" style="color:${diffColor}">${diff === null ? '—' : diffSym + ' ' + Math.abs(diff).toFixed(mode==='gas'?1:0) + unit}</div></div>
<div class="sc"><div class="sv">${y2Val}</div><div class="sl">去年同期${unitLabel}</div></div>
<div class="sc"><div class="sv${ld.balance!==null&&ld.balance<10?' w':''}">${ld.balance!==null?'¥'+ld.balance.toFixed(2):'--'}</div><div class="sl">余额</div></div>
<div class="sc"><div class="sv">${ld.latestUsage!==null?ld.latestUsage.toFixed(1)+'m³':'--'}</div><div class="sl">${periodLabel}用气</div></div>
<div class="sc"><div class="sv">${ld.latestBill!==null?'¥'+ld.latestBill.toFixed(0):'--'}</div><div class="sl">${periodLabel}费用</div></div>
</div>
${_tierProgress(ld)}
<div class="f">
<div class="fr"><span>${y1}年用气总计</span><span class="fv">${y1Total === null ? '—' : y1Total.toFixed(1) + ' m³'}</span></div>
<div class="fr"><span>${y1}年费用</span><span class="fv">${_money(y1Cost)}</span></div>
${ld.status?`<div class="fr"><span>集成状态</span><span class="fv" style="color:${ld.status==='normal'||ld.status==='正常'?'#4caf50':'#f44336'}">${ld.status}</span></div>`:''}
</div>`:''}
</div></ha-card></div>`;
  }

  _cy(d) {
    this._year += d;
    this._selectedMonth = null;
    this._loading = true;
    this._render();
    Promise.all([
      this._loadYear(this._year).catch(() => {}),
      !this._yearData[this._year-1] ? this._loadYear(this._year-1).catch(() => {}) : Promise.resolve()
    ]).then(() => { this._loading = false; this._render(); });
  }

  getCardSize() { return 7; }
}

customElements.define('crcgas-statistics-card', CrcgasStatisticsCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: 'crcgas-statistics-card', name: '华润燃气统计', description: '华润燃气年度对比统计 · 通用版（自动检测实体）' });
