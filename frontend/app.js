'use strict';
/* VisionCheckout POS — giao diện một trang (HTML/JS thuần, không cần build).
   Mọi chuỗi động đi qua esc() (chống XSS). Hàm thuần được export qua root.__pos để test bằng Node. */
(function (root) {
  // ======================================================================= hàm thuần
  const ERR = {
    AUTH_INVALID: 'Sai tài khoản hoặc mật khẩu', RATE_LIMITED: 'Đăng nhập sai quá nhiều lần, hãy thử lại sau',
    PRICE_MISSING_BLOCKED: 'Còn sản phẩm chưa có giá, hãy nhập giá tay', QUEUE_FULL: 'Hệ thống đang bận, thử lại sau ít giây',
    IMAGE_TOO_LARGE: 'Ảnh quá lớn, hãy chụp lại', IMAGE_DECODE_ERROR: 'Không đọc được ảnh, hãy chụp lại',
    NETWORK: 'Mất kết nối tới máy chủ', ORDER_NOT_OPEN: 'Đơn hàng đã đóng', BARCODE_DUPLICATE: 'Barcode đã thuộc sản phẩm khác',
    FORBIDDEN: 'Bạn không có quyền thực hiện', GPU_OOM: 'Hệ thống quá tải, hãy chụp lại', PIPELINE_ERROR: 'Không xử lý được ảnh, hãy chụp lại',
  };
  const LOGOUT_MSG = {
    SESSION_INVALID: 'Tài khoản đã đăng nhập ở thiết bị khác hoặc ca đã kết thúc',
    AUTH_EXPIRED: 'Phiên đăng nhập đã hết hạn, hãy đăng nhập lại', AUTH_INVALID: 'Phiên đăng nhập không hợp lệ',
  };
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const p2 = (n) => String(n).padStart(2, '0');
  const fmtMoney = (n) => (n == null ? '—' : Number(n).toLocaleString('vi-VN') + 'đ');
  const dayKey = (iso) => { const d = new Date(iso); return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()); };
  const hm = (iso) => { const d = new Date(iso); return p2(d.getHours()) + ':' + p2(d.getMinutes()); };
  function dayLabel(key, now) {
    now = now || new Date();
    const yest = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    const k = (d) => d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate());
    if (key === k(now)) return 'Hôm nay';
    if (key === k(yest)) return 'Hôm qua';
    const [y, m, d] = key.split('-'); return d + '/' + m + '/' + y;
  }
  function groupByDay(items, now) {
    const map = new Map();
    for (const it of items) {
      const key = dayKey(it.created_at);
      if (!map.has(key)) map.set(key, { key, label: dayLabel(key, now), total: 0, count: 0, items: [] });
      const g = map.get(key); g.items.push(it);
      if (it.status === 'paid') { g.total += it.total || 0; g.count += 1; }
    }
    return [...map.values()];
  }
  const tiltAngle = (beta, gamma) => Math.hypot(beta || 0, gamma || 0);
  const parseMoney = (t) => { const d = String(t == null ? '' : t).replace(/\D/g, ''); return d === '' ? null : parseInt(d, 10); };
  const uuid = () => (root.crypto && root.crypto.randomUUID ? root.crypto.randomUUID()
    : 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const TILT_LIMIT = 15;
  const MAX_CASH = 999999999; // phải nhỏ hơn mức trần của backend (1.000.000.000)

  // ======================================================================= trạng thái + API
  const S = {
    token: null, user: null, settings: { allow_checkout_without_price: false }, screen: 'login', loginMsg: '',
    order: null, job: null, sheet: null, pay: { method: 'cash', given: 0 }, hist: { range: 'today', items: [] },
    onb: 0, camOk: null, tilt: null, busy: false, overlap: false, unrec: 0,
    admin: { tab: 'reports', data: null, search: '', filter: '', page: 1, range: 'today', repRange: '7d' },
  };
  let stream = null, orientHandler = null, timers = [], scanBuf = '', scanLast = 0, searchTimer = null;
  const store = (() => { try { return root.sessionStorage; } catch (e) { return null; } })();

  class ApiErr extends Error {
    constructor(status, code, message, extra) { super(message); this.status = status; this.code = code; this.extra = extra || {}; }
  }
  const errText = (e) => (e && (ERR[e.code] || e.message)) || 'Có lỗi xảy ra';

  async function api(method, path, body, opt) {
    opt = opt || {};
    const headers = Object.assign({}, opt.headers || {});
    if (S.token) headers.Authorization = 'Bearer ' + S.token;
    let payload;
    if (opt.raw) { payload = body; headers['Content-Type'] = 'image/jpeg'; }
    else if (body !== undefined) { payload = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
    let res;
    try { res = await fetch(path, { method, headers, body: payload }); }
    catch (e) { throw new ApiErr(0, 'NETWORK', ERR.NETWORK); }
    const ct = (res.headers.get('content-type') || '');
    const data = ct.includes('json') ? await res.json() : null;
    if (!res.ok) {
      const e = (data && data.error) || {};
      const err = new ApiErr(res.status, e.code || 'ERROR', e.message || 'Có lỗi xảy ra', e);
      if (S.token && LOGOUT_MSG[err.code]) forceLogout(err.code);
      throw err;
    }
    return data;
  }

  // ======================================================================= DOM (chỉ khi có document)
  const hasDom = typeof document !== 'undefined';
  const $ = (s) => document.querySelector(s);

  function toast(msg, bad) {
    if (!hasDom) return;
    const t = $('#toast'); if (!t) return;
    t.textContent = msg; t.className = 'show' + (bad ? ' bad' : '');
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.className = ''; }, 3200);
  }
  function modal(html, mid) {
    $('#modal-root').innerHTML = '<div class="overlay' + (mid ? ' mid' : '') + '" data-act="closeOverlay">' +
      '<div class="sheet' + (mid ? ' center' : '') + '" data-stop="1">' + html + '</div></div>';
  }
  const closeModal = () => { S.sheet = null; $('#modal-root').innerHTML = ''; };
  const clearTimers = () => { timers.forEach((t) => clearInterval(t)); timers = []; };

  function go(screen) {
    if (S.screen === 'viewfinder' && screen !== 'viewfinder') stopCamera();
    if (S.screen === 'admin' && screen !== 'admin') clearTimers();
    S.screen = screen; render();
  }
  function forceLogout(code) {
    S.token = null; S.user = null; S.order = null; if (store) store.removeItem('tok');
    stopCamera(); clearTimers();
    S.loginMsg = LOGOUT_MSG[code] || ''; S.screen = 'login';
    if (hasDom) { closeModal(); render(); }
  }

  // ---------------------------------------------------------------- khung POS
  function posShell(inner, title) {
    const u = S.user || {};
    return '<div class="pos"><div class="topbar"><div class="avatar">' + esc((u.full_name || u.username || '?').trim().charAt(0).toUpperCase()) + '</div>' +
      '<div class="grow"><div style="font-weight:700">' + esc(u.full_name || u.username) + '</div><div class="muted small">' + esc(title || 'Thu ngân') + '</div></div>' +
      '<button class="icon" data-act="toHistory" title="Lịch sử">🕘</button>' +
      (u.role === 'admin' ? '<button class="icon" data-act="toAdmin" title="Quản trị">⚙️</button>' : '') +
      '<button class="icon danger" data-act="logout" title="Đóng ca">⏻</button></div>' +
      '<div class="content">' + inner + '</div></div>';
  }

  // ---------------------------------------------------------------- các màn
  function vLogin() {
    return '<div class="login"><div class="brand">Vision<b>Checkout</b></div><p class="muted">Thanh toán cả rổ hàng chỉ bằng một tấm ảnh.</p>' +
      '<form id="loginForm"><input name="u" autocomplete="username" autocapitalize="none" placeholder="Tài khoản" required>' +
      '<input name="p" type="password" autocomplete="current-password" placeholder="Mật khẩu" required>' +
      '<div class="err" id="loginErr">' + esc(S.loginMsg) + '</div><button class="primary" type="submit">Vào ca làm việc</button></form></div>';
  }
  const ONB = [
    ['📦', 'Đặt hàng vào khung', 'Xếp các món lên bàn, tránh chồng lên nhau, giữ điện thoại thẳng phía trên.'],
    ['📸', 'Chụp một lần', 'Không cần quét từng món. Bấm chụp, hệ thống nhận diện cả rổ hàng.'],
    ['⚠️', 'Viền vàng = cần xác nhận', 'Chạm vào dòng có viền vàng để xác nhận hoặc chọn lại sản phẩm đúng.'],
    ['💵', 'Thanh toán', 'Kiểm tra hoá đơn, chọn tiền mặt hoặc chuyển khoản, xong.'],
  ];
  function vOnboarding() {
    const [ic, t, d] = ONB[S.onb], last = S.onb === ONB.length - 1;
    return '<div class="login" style="text-align:center"><div style="font-size:64px">' + ic + '</div><h2 class="gap">' + esc(t) + '</h2>' +
      '<p class="muted">' + esc(d) + '</p><div class="row gap"><button class="ghost grow" data-act="onbSkip">Bỏ qua</button>' +
      '<button class="primary grow" data-act="onbNext">' + (last ? 'Bắt đầu' : 'Tiếp') + '</button></div></div>';
  }
  function vViewfinder() {
    const n = S.order && S.order.item_count;
    return posShell('<div class="vf" id="vf"><video id="cam" playsinline muted autoplay></video><div class="frame" id="frame"></div>' +
      '<div class="badge" id="tiltBadge">Đặt hàng vào khung rồi bấm chụp</div>' +
      '<button class="shutter" data-act="shoot" aria-label="Chụp"></button></div>' +
      '<div class="nocam card hidden gap" id="nocam"><p><b>Không mở được camera.</b><br><span class="muted small">Cần cấp quyền camera và mở trang qua HTTPS. Bạn vẫn có thể chọn ảnh từ máy.</span></p></div>' +
      '<div class="row gap"><button class="ghost grow" data-act="pickFile">🖼️ Chọn ảnh</button>' +
      '<button class="ghost hidden" id="tiltBtn" data-act="tiltPerm">Bật cảm biến nghiêng</button>' +
      (n ? '<button class="primary grow" data-act="toInvoice">Về hoá đơn (' + n + ')</button>' : '') + '</div>' +
      '<input type="file" id="file" accept="image/*" capture="environment" class="hidden">', 'Chụp rổ hàng');
  }
  function vLoading() {
    const j = S.job || {};
    return posShell('<div class="skel"></div><div class="skel gap"></div><div class="skel gap"></div>' +
      '<p class="muted gap" style="text-align:center">Đang nhận diện sản phẩm…' + (j.pos ? ' (đứng thứ ' + (j.pos + 1) + ' trong hàng chờ)' : '') +
      (j.slow ? '<br><span class="small">Có thể lâu hơn bình thường</span>' : '') + '</p>', 'Đang xử lý');
  }
  function itemRow(it) {
    const th = it.thumbnail_url ? '<div class="thumb" style="background-image:url(\'' + esc(it.thumbnail_url) + '\')"></div>' : '<div class="thumb">IMG</div>';
    const price = it.price_missing
      ? '<input class="pin need" inputmode="numeric" placeholder="Nhập giá" data-in="price" data-item="' + it.id + '" aria-label="Giá">'
      : '<div class="price">' + fmtMoney(it.unit_price) + (it.manual_price ? ' (giá tay)' : '') + '</div>';
    return '<div class="item' + (it.flagged ? ' flag' : '') + '" data-act="' + (it.flagged ? 'openFix' : 'none') + '" data-item="' + it.id + '">' + th +
      '<div><div class="name">' + (it.flagged ? '<span class="warn">⚠ </span>' : '') + esc(it.product_name) + '</div>' + price + '</div>' +
      '<div class="stepper"><button data-act="qty" data-item="' + it.id + '" data-d="-1" aria-label="Giảm">−</button><span class="q">' + it.quantity +
      '</span><button data-act="qty" data-item="' + it.id + '" data-d="1" aria-label="Tăng">+</button></div></div>';
  }
  function vInvoice() {
    const o = S.order;
    const blocked = o.missing_price_count > 0 && !S.settings.allow_checkout_without_price;
    return posShell('<div class="row"><h3 class="grow">Đơn #' + o.id + '</h3><span class="muted small">' + hm(o.created_at) + '</span></div>' +
      (S.unrec ? '<div class="banner gap">🔎 Phát hiện thêm ' + S.unrec + ' vật chưa nhận diện được (có thể là sản phẩm chưa có trong hệ thống, hoặc ảnh chưa rõ). Hãy chụp gần hơn hoặc thêm món thủ công.' +
        '<div class="row" style="margin-top:8px"><button class="primary" data-act="openAdd">＋ Thêm món</button><button class="ghost" data-act="dismissUnrec">Bỏ qua</button></div></div>' : '') +
      (S.overlap ? '<div class="banner gap">📷 Có sản phẩm chồng lên nhau — nên chụp thêm để nhận diện chính xác hơn.' +
        '<div class="row" style="margin-top:8px"><button class="primary" data-act="toCamera">Chụp thêm</button><button class="ghost" data-act="dismissOverlap">Bỏ qua</button></div></div>' : '') +
      (o.flagged_count ? '<div class="banner gap">⚠ Có ' + o.flagged_count + ' dòng cần xác nhận — chạm vào dòng viền vàng.</div>' : '') +
      '<div class="items">' + (o.items.length ? o.items.map(itemRow).join('') :
        '<div class="card muted" style="text-align:center">Chưa có sản phẩm. Hãy chụp thêm hoặc thêm thủ công.</div>') + '</div>' +
      '<div class="row"><button class="ghost grow" data-act="toCamera">📸 Chụp thêm</button><button class="ghost grow" data-act="openAdd">＋ Thêm món</button></div>' +
      '<div class="footer"><div class="row"><div class="grow muted">' + o.item_count + ' sản phẩm</div><div class="total">' + fmtMoney(o.total) + '</div></div>' +
      '<div class="row gap"><button class="danger" data-act="newOrder">Huỷ đơn</button><button class="primary grow" data-act="toPay"' +
      (!o.items.length ? ' disabled' : '') + '>' + (blocked ? 'Nhập giá cho ' + o.missing_price_count + ' món' : 'Thanh toán') + '</button></div></div>', 'Hoá đơn');
  }
  const QUICK = [50000, 100000, 200000, 500000];
  function vPay() {
    const o = S.order, p = S.pay, change = p.given - o.total;
    const cash = '<div class="card gap"><div class="muted small">Khách đưa</div><div class="big" id="given">' + fmtMoney(p.given) + '</div>' +
      '<div class="row"><span class="muted">Tiền thừa</span><span class="grow right ' + (change >= 0 ? 'ok' : 'warn') + '" id="change" style="font-weight:700;font-size:20px">' +
      (change >= 0 ? fmtMoney(change) : 'Còn thiếu ' + fmtMoney(-change)) + '</span></div></div>' +
      '<div class="quick">' + QUICK.map((v) => '<button data-act="quick" data-v="' + v + '">' + v / 1000 + 'k</button>').join('') + '</div>' +
      '<div class="numpad">' + ['1', '2', '3', '4', '5', '6', '7', '8', '9', '000', '0', '⌫'].map((k) => '<button data-act="key" data-k="' + k + '">' + k + '</button>').join('') + '</div>' +
      '<button class="primary gap" style="width:100%" data-act="confirmPay" id="confirmBtn"' + (change < 0 ? ' disabled' : '') + '>Xác nhận thanh toán</button>';
    const qr = '<div class="qr">QR mô phỏng</div><p class="muted" style="text-align:center">Đang chờ xác nhận chuyển khoản…</p>' +
      '<button class="primary" style="width:100%" data-act="confirmPay">Đã nhận được tiền</button>';
    return posShell('<div class="muted">Tổng thanh toán</div><div class="big">' + fmtMoney(o.total) + '</div>' +
      '<div class="seg gap"><button data-act="method" data-m="cash" class="' + (p.method === 'cash' ? 'on' : '') + '">💵 Tiền mặt</button>' +
      '<button data-act="method" data-m="qr" class="' + (p.method === 'qr' ? 'on' : '') + '">📱 Chuyển khoản</button></div>' +
      (p.method === 'cash' ? cash : qr) + '<button class="ghost gap" style="width:100%" data-act="toInvoice">Huỷ</button>', 'Thanh toán');
  }
  function vDone() {
    const o = S.order;
    return posShell('<div style="text-align:center;padding-top:24px"><div style="font-size:56px">✅</div><h2>Thanh toán thành công</h2>' +
      '<div class="big gap">' + fmtMoney(o.total) + '</div><p class="muted">' + (o.payment_method === 'cash' ? 'Tiền mặt' : 'Chuyển khoản') + '</p>' +
      (o.payment_method === 'cash' ? '<p>Khách đưa <b>' + fmtMoney(o.cash_given) + '</b> · Tiền thừa <b class="ok">' + fmtMoney(o.change_given) + '</b></p>' : '') +
      '<div class="row gap"><button class="ghost grow" data-act="print">🖨️ In hoá đơn</button><button class="primary grow" data-act="newAfterDone">Đơn mới</button></div></div>', 'Hoàn tất');
  }
  function vHistory() {
    const h = S.hist, groups = groupByDay(h.items);
    return posShell('<div class="row"><select id="histRange" data-in="histRange"><option value="today"' + (h.range === 'today' ? ' selected' : '') + '>Hôm nay</option>' +
      '<option value="7d"' + (h.range === '7d' ? ' selected' : '') + '>7 ngày</option><option value="30d"' + (h.range === '30d' ? ' selected' : '') + '>30 ngày</option></select>' +
      '<button class="ghost" data-act="backFromHistory">Quay lại</button></div>' +
      (groups.length ? groups.map((g) => '<div class="day"><span>' + esc(g.label) + '</span><span>' + g.count + ' đơn · ' + fmtMoney(g.total) + '</span></div>' +
        g.items.map((x) => '<button class="hrow" data-act="viewOrder" data-id="' + x.id + '"><div><b>#' + x.id + '</b> · ' + hm(x.created_at) + ' · ' + x.item_count +
          ' món</div><div><span class="tag ' + esc(x.status) + '">' + (x.status === 'paid' ? 'Đã thu' : 'Đã huỷ') + '</span> <b>' + fmtMoney(x.total) + '</b></div></button>').join('')).join('')
        : '<p class="muted gap" style="text-align:center">Chưa có đơn nào.</p>'), 'Lịch sử');
  }

  // ---------------------------------------------------------------- admin
  const TABS = [['reports', '📊 Báo cáo'], ['products', '📦 Sản phẩm'], ['orders', '🧾 Đơn hàng'], ['users', '👥 Nhân viên'], ['settings', '⚙️ Cài đặt']];
  function vAdmin() {
    const a = S.admin, d = a.data;
    let body = '<p class="muted">Đang tải…</p>';
    if (d) body = ({ reports: aReports, products: aProducts, orders: aOrders, users: aUsers, settings: aSettings })[a.tab](d);
    return '<div class="adm"><div class="side"><div class="brand" style="font-size:20px">Vision<b>Checkout</b></div><div class="muted small" style="margin-bottom:8px">' +
      esc((S.user || {}).full_name) + '</div>' + TABS.map(([k, l]) => '<button data-act="tab" data-t="' + k + '" class="' + (a.tab === k ? 'on' : '') + '">' + l + '</button>').join('') +
      '<div class="grow"></div><button data-act="toCamera">🛒 Chế độ thu ngân</button><button class="danger" data-act="logout">Đăng xuất</button></div><div class="main">' + body + '</div></div>';
  }
  const kpi = (l, v, w) => '<div class="card kpi"><span class="muted small">' + l + '</span><b' + (w ? ' class="warn"' : '') + '>' + v + '</b></div>';
  function aReports(d) {
    const a = S.admin, max = Math.max(1, ...d.daily.map((x) => x.revenue)), n = d.daily.length;
    const bars = d.daily.map((x, i) => '<div class="barwrap" title="' + esc(x.date) + ': ' + esc(fmtMoney(x.revenue)) + ' · ' + x.orders + ' đơn">' +
      '<div class="bar" style="height:' + Math.max(1, Math.round(x.revenue / max * 100)) + '%"></div>' +
      '<span class="muted small">' + (n <= 7 || i % 5 === 0 || i === n - 1 ? esc(x.date.slice(8) + '/' + x.date.slice(5, 7)) : '&nbsp;') + '</span></div>').join('');
    const top = d.top_products.length ? '<table><tr><th>Sản phẩm</th><th>Số lượng</th><th>Doanh thu</th></tr>' + d.top_products.map((t) =>
      '<tr><td>' + esc(t.name) + '</td><td>' + t.quantity + '</td><td>' + fmtMoney(t.revenue) + '</td></tr>').join('') + '</table>' : '<p class="muted">Chưa có đơn nào trong khoảng này.</p>';
    return aReportsToday(d) + '<div class="row gap"><h2 class="grow">Doanh thu theo ngày</h2><select data-in="repRange" style="max-width:150px">' +
      [['today', 'Hôm nay'], ['7d', '7 ngày'], ['30d', '30 ngày']].map(([k, l]) => '<option value="' + k + '"' + (a.repRange === k ? ' selected' : '') + '>' + l + '</option>').join('') + '</select></div>' +
      '<div class="kpis">' + kpi('Doanh thu', fmtMoney(d.range_revenue)) + kpi('Số đơn', d.range_orders) + '</div>' +
      '<div class="card gap"><div class="bars">' + bars + '</div></div><h3 class="gap">Top sản phẩm bán chạy</h3>' + top;
  }
  function aReportsToday(d) {
    return '<h2>Báo cáo hôm nay</h2><div class="kpis">' + kpi('Đơn đã thu', d.orders_today) + kpi('Doanh thu', fmtMoney(d.revenue_today)) + kpi('Số ảnh xử lý', d.captures_today) +
      kpi('Tỉ lệ lỗi', (d.error_rate * 100).toFixed(1) + '%', d.error_rate > 0.1) + kpi('Độ trễ TB', d.avg_processing_ms == null ? '—' : Math.round(d.avg_processing_ms) + ' ms') +
      kpi('Ca đang mở', d.active_shifts) + kpi('Hàng chờ', d.queue_size) + kpi('SKU thiếu giá', d.products_missing_price + '/' + d.products_total, d.products_missing_price > 0) +
      kpi('SKU thiếu barcode', d.products_missing_barcode + '/' + d.products_total) + '</div>';
  }
  function aProducts(d) {
    const a = S.admin, pages = Math.max(1, Math.ceil(d.total / d.size));
    const chip = (k, l) => '<button data-act="filter" data-f="' + k + '" class="' + (a.filter === k ? 'on' : '') + '">' + l + '</button>';
    return '<h2>Sản phẩm</h2><div class="row gap"><input id="pSearch" data-in="pSearch" placeholder="Tìm theo tên / mã / barcode" value="' + esc(a.search) + '"></div>' +
      '<div class="chips">' + chip('', 'Tất cả (' + d.total + ')') + chip('missing_price', 'Thiếu giá (' + d.missing_price + ')') + chip('missing_barcode', 'Thiếu barcode (' + d.missing_barcode + ')') + '</div>' +
      '<table><tr><th>Mã</th><th>Tên</th><th>Barcode</th><th>Giá (đ)</th><th></th></tr>' + d.items.map((p) =>
        '<tr><td>' + esc(p.id) + '</td><td>' + esc(p.name) + '</td><td><input data-f="barcode" data-pid="' + esc(p.id) + '" value="' + esc(p.barcode) + '" placeholder="—"></td>' +
        '<td><input data-f="price" data-pid="' + esc(p.id) + '" inputmode="numeric" value="' + (p.price == null ? '' : p.price) + '" placeholder="chưa có giá"></td>' +
        '<td class="row"><button class="primary" data-act="saveProduct" data-pid="' + esc(p.id) + '">Lưu</button><button data-act="history" data-pid="' + esc(p.id) + '" title="Lịch sử thay đổi">🕘</button></td></tr>').join('') + '</table>' +
      '<div class="row gap"><button class="ghost" data-act="page" data-d="-1"' + (a.page <= 1 ? ' disabled' : '') + '>‹</button><span class="muted">Trang ' + a.page + '/' + pages +
      '</span><button class="ghost" data-act="page" data-d="1"' + (a.page >= pages ? ' disabled' : '') + '>›</button></div>';
  }
  function aOrders(d) {
    return '<h2>Đơn hàng</h2><div class="row gap"><select data-in="admRange"><option value="today"' + (S.admin.range === 'today' ? ' selected' : '') + '>Hôm nay</option>' +
      '<option value="7d"' + (S.admin.range === '7d' ? ' selected' : '') + '>7 ngày</option><option value="30d"' + (S.admin.range === '30d' ? ' selected' : '') + '>30 ngày</option>' +
      '<option value="all"' + (S.admin.range === 'all' ? ' selected' : '') + '>Tất cả</option></select></div>' +
      '<table><tr><th>Đơn</th><th>Giờ</th><th>Thu ngân</th><th>Món</th><th>Tổng</th><th>Trạng thái</th><th></th></tr>' + d.items.map((x) =>
        '<tr><td>#' + x.id + '</td><td>' + new Date(x.created_at).toLocaleString('vi-VN') + '</td><td>' + esc(x.cashier) + '</td><td>' + x.item_count + '</td><td>' + fmtMoney(x.total) +
        '</td><td><span class="tag ' + esc(x.status) + '">' + (x.status === 'paid' ? 'Đã thu' : 'Đã huỷ') + '</span></td><td><button data-act="viewOrder" data-id="' + x.id + '">Chi tiết</button></td></tr>').join('') + '</table>';
  }
  function aUsers(d) {
    return '<h2>Nhân viên</h2><div class="card gap"><div class="row"><input id="nu" placeholder="Tài khoản" style="max-width:160px"><input id="nn" placeholder="Họ tên">' +
      '<input id="np" type="password" placeholder="Mật khẩu (≥8)" style="max-width:170px"><select id="nr" style="max-width:110px"><option value="staff">staff</option><option value="admin">admin</option></select>' +
      '<button class="primary" data-act="createUser">Thêm</button></div></div><table><tr><th>Tài khoản</th><th>Họ tên</th><th>Vai trò</th><th>Trạng thái</th><th></th></tr>' +
      d.items.map((u) => '<tr><td>' + esc(u.username) + '</td><td>' + esc(u.full_name) + '</td><td>' + esc(u.role) + '</td><td>' + (u.is_active ? (u.online ? '🟢 Đang trong ca' : 'Hoạt động') : '⛔ Đã khoá') +
        '</td><td class="row"><button data-act="resetPw" data-id="' + u.id + '">Đặt lại MK</button><button data-act="toggleUser" data-id="' + u.id + '" data-on="' + (u.is_active ? 1 : 0) + '">' + (u.is_active ? 'Khoá' : 'Mở khoá') + '</button></td></tr>').join('') + '</table>';
  }
  function aSettings(d) {
    const dflt = d.similarity_threshold == null, mdef = d.min_confidence_accept == null;
    return '<h2>Cài đặt</h2>' +
      '<div class="toggle"><div><b>Chặn chụp khi máy nghiêng</b><div class="muted small">Bật: không cho bấm chụp khi cảm biến báo nghiêng quá 15°. Tắt: chỉ cảnh báo.</div></div><input type="checkbox" id="sTilt" style="width:auto"' + (d.tilt_block_capture ? ' checked' : '') + '></div>' +
      '<div class="toggle"><div><b>Tự động in hoá đơn</b><div class="muted small">Mở hộp thoại in ngay sau khi thanh toán xong.</div></div><input type="checkbox" id="sPrint" style="width:auto"' + (d.auto_print_receipt ? ' checked' : '') + '></div>' +
      '<div class="toggle"><div><b>Cho phép thanh toán khi thiếu giá</b><div class="muted small">Tắt (khuyến nghị): bắt buộc nhập giá tay trước khi thanh toán.</div></div>' +
      '<input type="checkbox" id="sAllow" style="width:auto"' + (d.allow_checkout_without_price ? ' checked' : '') + '></div>' +
      '<div class="toggle"><div><b>Ngưỡng nhận diện (similarity)</b><div class="muted small">Cao hơn = nhiều dòng viền vàng hơn, ít nhận sai hơn. Áp dụng ngay ở lần chụp kế tiếp.</div></div>' +
      '<div style="min-width:240px"><label class="row"><input type="checkbox" id="sDef" style="width:auto"' + (dflt ? ' checked' : '') + '> Dùng mặc định của hệ thống</label>' +
      '<input type="range" id="sThr" min="0.30" max="0.95" step="0.01" value="' + (dflt ? 0.6 : d.similarity_threshold) + '"' + (dflt ? ' disabled' : '') + ' data-in="thr"><div class="muted small" id="sThrV">' + (dflt ? 'mặc định' : d.similarity_threshold) + '</div></div></div>' +
      '<div class="toggle"><div><b>Độ tin cậy tối thiểu để tự chấp nhận</b><div class="muted small">Cao hơn = nhiều dòng viền vàng hơn, ít chấp nhận nhầm hơn. Áp dụng ngay ở lần chụp kế tiếp.</div></div>' +
      '<div style="min-width:240px"><label class="row"><input type="checkbox" id="sMinDef" style="width:auto"' + (mdef ? ' checked' : '') + '> Dùng mặc định của hệ thống</label>' +
      '<input type="range" id="sMinThr" min="0.30" max="0.99" step="0.01" value="' + (mdef ? 0.5 : d.min_confidence_accept) + '"' + (mdef ? ' disabled' : '') + ' data-in="minthr"><div class="muted small" id="sMinV">' + (mdef ? 'mặc định' : d.min_confidence_accept) + '</div></div></div>' +
      '<button class="primary gap" data-act="saveSettings">Lưu cài đặt</button>';
  }

  // ---------------------------------------------------------------- render + vòng đời màn
  function render() {
    if (!hasDom) return;
    const views = { login: vLogin, onboarding: vOnboarding, viewfinder: vViewfinder, loading: vLoading, invoice: vInvoice, pay: vPay, done: vDone, history: vHistory, admin: vAdmin };
    const fn = views[S.screen] || vLogin;
    $('#app').innerHTML = fn();
    if (S.screen === 'viewfinder') startCamera();
  }

  // ---------------------------------------------------------------- camera + nghiêng
  async function startCamera() {
    const video = $('#cam'); if (!video) return;
    S.tilt = null;
    const fail = () => { S.camOk = false; const v = $('#vf'), n = $('#nocam'); if (v) v.classList.add('hidden'); if (n) n.classList.remove('hidden'); };
    if (!(root.navigator && navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return fail();
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
      if (S.screen !== 'viewfinder') { stopCamera(); return; }
      video.srcObject = stream; S.camOk = true; await video.play().catch(() => {});
    } catch (e) { return fail(); }
    startTilt();
  }
  function stopCamera() {
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    if (orientHandler) { root.removeEventListener('deviceorientation', orientHandler); orientHandler = null; }
  }
  function startTilt() {
    const DO = root.DeviceOrientationEvent; if (!DO) return;
    if (typeof DO.requestPermission === 'function') { const b = $('#tiltBtn'); if (b) b.classList.remove('hidden'); return; }
    listenTilt();
  }
  function listenTilt() {
    if (orientHandler) return;
    let last = 0;
    orientHandler = (e) => {
      if (Date.now() - last < 500) return; last = Date.now();
      const a = tiltAngle(e.beta, e.gamma), bad = a > TILT_LIMIT, b = $('#tiltBadge'), f = $('#frame');
      S.tilt = a; if (!b || !f) return;
      b.textContent = bad ? 'Nghiêng (' + Math.round(a) + '°) — hãy chỉnh thẳng' : 'Góc chụp chuẩn (' + Math.round(a) + '°)';
      b.className = 'badge' + (bad ? ' bad' : ''); f.className = 'frame' + (bad ? ' bad' : '');
    };
    root.addEventListener('deviceorientation', orientHandler);
  }
  function grabFrame() {
    const v = $('#cam'); if (!v || !v.videoWidth) return Promise.resolve(null);
    const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext('2d').drawImage(v, 0, 0);
    return new Promise((res) => c.toBlob(res, 'image/jpeg', 0.9));
  }

  // ---------------------------------------------------------------- khôi phục đơn đang mở + thu nhỏ ảnh
  async function resumeOrder() {
    try { S.order = (await api('GET', '/api/orders/open')).order; } catch (e) { S.order = null; }
    return S.order;
  }
  const MAX_SIDE = 2048, MAX_BYTES = 3 * 1024 * 1024;
  // Ảnh từ thư viện máy (12MP, vài MB) tải lên chậm qua 4G và có thể vượt giới hạn: thu nhỏ + sửa hướng EXIF trước khi gửi.
  async function prepareImage(blob) {
    if (!hasDom || !root.createImageBitmap || !blob) return blob;
    try {
      const bmp = await root.createImageBitmap(blob, { imageOrientation: 'from-image' });
      const big = Math.max(bmp.width, bmp.height);
      if (big <= MAX_SIDE && blob.size <= MAX_BYTES) { if (bmp.close) bmp.close(); return blob; }
      const k = Math.min(1, MAX_SIDE / big), c = document.createElement('canvas');
      c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
      c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height); if (bmp.close) bmp.close();
      const out = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.88));
      return out && out.size < blob.size ? out : blob;
    } catch (e) { return blob; }
  }

  // ---------------------------------------------------------------- luồng chụp
  async function ensureOrder() {
    if (!S.order || S.order.status !== 'open') S.order = await api('POST', '/api/orders');
    return S.order;
  }
  async function submitPhoto(blob) {
    if (S.busy) return; S.busy = true;
    try {
      blob = await prepareImage(blob);
      const order = await ensureOrder();
      S.job = { pos: 0, slow: false }; S.overlap = false; S.unrec = 0; go('loading');
      const r = await api('POST', '/api/orders/' + order.id + '/captures', blob, { raw: true, headers: { 'Idempotency-Key': uuid() } });
      await pollJob(r.job_id);
    } catch (e) {
      toast(errText(e), true);
      if (S.token) go(S.order && S.order.items && S.order.items.length ? 'invoice' : 'viewfinder');
    } finally { S.busy = false; }
  }
  async function pollJob(id) {
    const t0 = Date.now();
    while (S.screen === 'loading') {
      const j = await api('GET', '/api/jobs/' + id);
      if (j.status === 'done') {
        S.order = j.order; S.overlap = (j.warnings || []).some((w) => w.type === 'overlap_detected');
        const un = (j.warnings || []).find((w) => w.type === 'unrecognized_objects'); S.unrec = un ? un.count : 0; go('invoice');
        api('GET', '/api/settings').then((s) => { S.settings = s; }).catch(() => {}); // áp dụng thay đổi cài đặt của admin
        if (!j.added) toast(S.unrec ? 'Phát hiện ' + S.unrec + ' vật nhưng chưa nhận diện được — hãy chụp gần hơn hoặc thêm thủ công' : 'Không thấy sản phẩm nào — hãy chụp lại hoặc thêm thủ công');
        return;
      }
      if (j.status === 'error') { toast(errText(j.error), true); go(S.order.items.length ? 'invoice' : 'viewfinder'); return; }
      S.job.pos = j.position;
      if (Date.now() - t0 > 3000 && !S.job.slow) { S.job.slow = true; render(); }
      if (Date.now() - t0 > 120000) { toast('Quá thời gian chờ, hãy chụp lại', true); go('viewfinder'); return; }
      await sleep(600);
    }
  }

  // ---------------------------------------------------------------- bottom sheet chọn sản phẩm
  async function searchProducts(q) {
    const r = await api('GET', '/api/catalog/products?search=' + encodeURIComponent(q || ''));
    if (!S.sheet) return; S.sheet.results = r.items; renderSheet();
  }
  function openSheet(mode, itemId) {
    S.sheet = { mode, itemId, results: [], msg: '' }; renderSheet(); searchProducts('').catch((e) => toast(errText(e), true));
  }
  function renderSheet() {
    const s = S.sheet; if (!s) return;
    const it = s.mode === 'fix' ? S.order.items.find((x) => x.id === s.itemId) : null;
    const keep = it ? '<div class="card"><div class="muted small">Hệ thống đoán</div><div class="name">' + esc(it.product_name) + '</div>' +
      '<button class="primary gap" style="width:100%" data-act="confirmItem" data-item="' + it.id + '">✓ Đúng, giữ nguyên</button></div><p class="muted small gap">Hoặc chọn sản phẩm khác:</p>' : '';
    modal('<h3>' + (it ? 'Xác nhận sản phẩm' : 'Thêm sản phẩm') + '</h3>' + keep +
      '<input class="gap" id="sheetQ" data-in="sheetQ" placeholder="Tìm theo tên" autocomplete="off">' +
      '<input class="gap" id="sheetB" data-in="sheetBarcode" placeholder="Hoặc nhập / quét mã vạch rồi Enter" inputmode="numeric" autocomplete="off">' +
      '<div class="err">' + esc(s.msg) + '</div><div class="plist">' + s.results.map((p) => '<button data-act="pick" data-id="' + esc(p.id) + '">' + esc(p.name) +
        '<small>' + (p.price == null ? 'chưa có giá' : fmtMoney(p.price)) + '</small></button>').join('') + '</div>' +
      '<button class="ghost gap" style="width:100%" data-act="closeModal">Đóng</button>');
    const q = $('#sheetQ'); if (q && s._q) { q.value = s._q; }
  }
  async function applyProduct(pid) {
    const s = S.sheet; if (!s) return;
    try {
      if (s.mode === 'fix') S.order = await api('PATCH', '/api/orders/' + S.order.id + '/items/' + s.itemId, { product_id: pid });
      else { await ensureOrder(); S.order = await api('POST', '/api/orders/' + S.order.id + '/items', { product_id: pid }); }
      closeModal(); render();
    } catch (e) { toast(errText(e), true); }
  }
  async function barcodeLookup(code, fromSheet) {
    code = String(code || '').trim(); if (!code) return;
    try {
      const r = await api('GET', '/api/catalog/products?barcode=' + encodeURIComponent(code));
      if (!r.items.length) {
        const m = 'Không có sản phẩm khớp mã này — thử tìm theo tên';
        if (fromSheet && S.sheet) { S.sheet.msg = m; renderSheet(); } else toast(m, true);
        return;
      }
      if (fromSheet && S.sheet) return applyProduct(r.items[0].id);
      await ensureOrder(); S.order = await api('POST', '/api/orders/' + S.order.id + '/items', { product_id: r.items[0].id });
      toast('Đã thêm: ' + r.items[0].name); if (S.screen === 'invoice') render(); else go('invoice');
    } catch (e) { toast(errText(e), true); }
  }

  // ---------------------------------------------------------------- hành động (uỷ quyền click)
  async function run(fn) { try { await fn(); } catch (e) { toast(errText(e), true); } }
  const A = {
    none() {},
    dismissOverlap() { S.overlap = false; render(); },
    dismissUnrec() { S.unrec = 0; render(); },
    closeOverlay(el, ev) { if (ev.target === el) closeModal(); },
    closeModal() { closeModal(); },
    async logout() {
      if (S.order && S.order.status === 'open' && S.order.items.length && !confirm('Đơn hiện tại chưa thanh toán. Huỷ đơn và đóng ca?')) return;
      if (S.order && S.order.status === 'open' && S.order.items.length) await api('POST', '/api/orders/' + S.order.id + '/void').catch(() => {});
      await api('POST', '/api/auth/logout').catch(() => {});
      forceLogout('');
    },
    onbNext() { if (S.onb < ONB.length - 1) { S.onb++; render(); } else A.onbSkip(); },
    async onbSkip() { api('POST', '/api/me/onboarding-seen').catch(() => {}); if (S.user) S.user.has_seen_onboarding = true; await enterPos(); },
    toHistory() { run(async () => { S.hist.items = (await api('GET', '/api/history?range=' + S.hist.range)).items; go('history'); }); },
    backFromHistory() { go(S.order && S.order.status === 'open' && S.order.items.length ? 'invoice' : 'viewfinder'); },
    toAdmin() { S.admin.tab = 'reports'; go('admin'); loadAdmin(); },
    async toCamera() { if (!S.order) await resumeOrder(); go('viewfinder'); },
    toInvoice() { go('invoice'); },
    pickFile() { $('#file').click(); },
    async shoot() {
      if (S.busy) return;
      if (S.settings.tilt_block_capture && S.tilt != null && S.tilt > TILT_LIMIT)
        return toast('Máy đang nghiêng (' + Math.round(S.tilt) + '°) — hãy giữ thẳng rồi chụp', true);
      const blob = await grabFrame();
      if (!blob) return toast('Camera chưa sẵn sàng', true);
      submitPhoto(blob);
    },
    async tiltPerm() {
      try { if ((await root.DeviceOrientationEvent.requestPermission()) === 'granted') { listenTilt(); $('#tiltBtn').classList.add('hidden'); } } catch (e) { /* bỏ qua */ }
    },
    openFix(el) { openSheet('fix', +el.dataset.item); },
    openAdd() { openSheet('add'); },
    confirmItem(el) { run(async () => { S.order = await api('PATCH', '/api/orders/' + S.order.id + '/items/' + el.dataset.item, { confirm: true }); closeModal(); render(); }); },
    pick(el) { applyProduct(el.dataset.id); },
    qty(el, ev) {
      ev.stopPropagation();
      const it = S.order.items.find((x) => x.id === +el.dataset.item), q = it.quantity + (+el.dataset.d);
      run(async () => {
        if (q < 1) { if (!confirm('Xoá "' + it.product_name + '" khỏi đơn?')) return; S.order = await api('DELETE', '/api/orders/' + S.order.id + '/items/' + it.id); }
        else S.order = await api('PATCH', '/api/orders/' + S.order.id + '/items/' + it.id, { quantity: q });
        render();
      });
    },
    async newOrder() {
      if (S.order && S.order.items.length && !confirm('Huỷ đơn hiện tại và bắt đầu đơn mới?')) return;
      if (S.order && S.order.status === 'open') await api('POST', '/api/orders/' + S.order.id + '/void').catch(() => {});
      S.order = null; S.overlap = false; S.unrec = 0; go('viewfinder');
    },
    toPay() {
      const o = S.order;
      if (o.missing_price_count && !S.settings.allow_checkout_without_price) {
        const first = document.querySelector('.pin.need'); if (first) { first.scrollIntoView({ block: 'center' }); first.focus(); }
        return toast(ERR.PRICE_MISSING_BLOCKED, true);
      }
      S.pay = { method: 'cash', given: 0 }; go('pay');
    },
    method(el) { S.pay.method = el.dataset.m; render(); },
    quick(el) { S.pay.given = +el.dataset.v; render(); },
    key(el) {
      const k = el.dataset.k; let g = String(S.pay.given || '');
      g = k === '⌫' ? g.slice(0, -1) : (g + k).replace(/^0+/, '');
      S.pay.given = Math.min(parseInt(g || '0', 10), MAX_CASH); render();
    },
    confirmPay() {
      if (S.busy) return; S.busy = true;
      const body = S.pay.method === 'cash' ? { method: 'cash', cash_given: S.pay.given } : { method: 'qr' };
      run(async () => { S.order = await api('POST', '/api/orders/' + S.order.id + '/checkout', body); go('done'); if (S.settings.auto_print_receipt) setTimeout(() => A.print(), 300); }).finally(() => { S.busy = false; });
    },
    print() {
      const o = S.order;
      $('#print-area').innerHTML = '<b>VisionCheckout</b><br>Đơn #' + o.id + ' · ' + new Date(o.paid_at || o.created_at).toLocaleString('vi-VN') + '<hr>' +
        o.items.map((i) => esc(i.product_name) + '<br>&nbsp;&nbsp;' + i.quantity + ' x ' + fmtMoney(i.unit_price) + ' = ' + fmtMoney(i.line_total)).join('<br>') +
        '<hr><b>TỔNG: ' + fmtMoney(o.total) + '</b><br>' + (o.payment_method === 'cash' ? 'Tiền mặt: ' + fmtMoney(o.cash_given) + '<br>Tiền thừa: ' + fmtMoney(o.change_given) : 'Chuyển khoản');
      root.print();
    },
    newAfterDone() { S.order = null; S.overlap = false; S.unrec = 0; go('viewfinder'); },
    viewOrder(el) {
      run(async () => {
        const o = await api('GET', '/api/orders/' + el.dataset.id);
        modal('<h3>Đơn #' + o.id + '</h3><div class="muted small">' + new Date(o.created_at).toLocaleString('vi-VN') + ' · ' + esc(o.status) + '</div>' +
          o.items.map((i) => '<div class="row gap"><div class="grow">' + esc(i.product_name) + '<div class="muted small">' + i.quantity + ' × ' + fmtMoney(i.unit_price) + '</div></div><b>' + fmtMoney(i.line_total) + '</b></div>').join('') +
          '<hr style="border-color:var(--line)"><div class="row"><b class="grow">Tổng</b><b>' + fmtMoney(o.total) + '</b></div>' +
          (S.user.role === 'admin' && o.status !== 'void' ? '<button class="danger gap" style="width:100%" data-act="voidOrder" data-id="' + o.id + '">Huỷ đơn này</button>' : '') +
          '<button class="ghost gap" style="width:100%" data-act="closeModal">Đóng</button>');
      });
    },
    voidOrder(el) { if (!confirm('Huỷ đơn #' + el.dataset.id + '? Không thể hoàn tác.')) return; run(async () => { await api('POST', '/api/orders/' + el.dataset.id + '/void'); closeModal(); if (S.screen === 'admin') loadAdmin(); else A.toHistory(); }); },
    // ---- admin
    tab(el) { S.admin.tab = el.dataset.t; S.admin.data = null; S.admin.page = 1; render(); loadAdmin(); },
    filter(el) { S.admin.filter = el.dataset.f; S.admin.page = 1; loadAdmin(); },
    page(el) { S.admin.page += +el.dataset.d; loadAdmin(); },
    history(el) { openHistory(el.dataset.pid); },
    revert(el) {
      if (!confirm('Hoàn tác thay đổi này?')) return;
      const pid = el.dataset.pid;
      run(async () => { await api('POST', '/api/admin/change-log/' + el.dataset.id + '/revert'); toast('Đã hoàn tác'); await openHistory(pid); loadAdmin(true); });
    },
    saveProduct(el) {
      const pid = el.dataset.pid, get = (f) => document.querySelector('[data-f="' + f + '"][data-pid="' + pid + '"]');
      const price = parseMoney(get('price').value);
      run(async () => { await api('PATCH', '/api/admin/products/' + encodeURIComponent(pid), { price, barcode: get('barcode').value.trim() }); toast('Đã lưu sản phẩm ' + pid); loadAdmin(true); });
    },
    createUser() {
      const v = (id) => $('#' + id).value;
      run(async () => { await api('POST', '/api/admin/users', { username: v('nu'), full_name: v('nn'), password: v('np'), role: v('nr') }); toast('Đã thêm nhân viên'); loadAdmin(); });
    },
    resetPw(el) { const pw = prompt('Mật khẩu mới (tối thiểu 8 ký tự):'); if (!pw) return; run(async () => { await api('PATCH', '/api/admin/users/' + el.dataset.id, { password: pw }); toast('Đã đặt lại mật khẩu'); loadAdmin(); }); },
    toggleUser(el) { run(async () => { await api('PATCH', '/api/admin/users/' + el.dataset.id, { is_active: el.dataset.on !== '1' }); loadAdmin(); }); },
    saveSettings() {
      const body = { allow_checkout_without_price: $('#sAllow').checked, tilt_block_capture: $('#sTilt').checked, auto_print_receipt: $('#sPrint').checked, similarity_threshold: $('#sDef').checked ? null : parseFloat($('#sThr').value),
        min_confidence_accept: $('#sMinDef').checked ? null : parseFloat($('#sMinThr').value) };
      run(async () => { S.settings = Object.assign(S.settings, await api('PATCH', '/api/admin/settings', body)); toast('Đã lưu cài đặt'); loadAdmin(); });
    },
  };

  const FIELD = { price: 'Giá', barcode: 'Barcode' };
  const showVal = (f, v) => (f === 'price' ? (v == null ? 'chưa có' : fmtMoney(Number(v))) : (v ? esc(v) : '—'));
  async function openHistory(pid) {
    try {
      const r = await api('GET', '/api/admin/change-log?table=product&record=' + encodeURIComponent(pid));
      modal('<h3>Lịch sử thay đổi · SKU ' + esc(pid) + '</h3>' + (r.items.length ? r.items.map((x) =>
        '<div class="row gap"><div class="grow"><b>' + esc(FIELD[x.field] || x.field) + '</b>: ' + showVal(x.field, x.old) + ' → <b>' + showVal(x.field, x.new) + '</b>' +
        '<div class="muted small">' + esc(x.by || '?') + ' · ' + new Date(x.at).toLocaleString('vi-VN') + '</div></div>' +
        '<button data-act="revert" data-id="' + x.id + '" data-pid="' + esc(pid) + '">Hoàn tác</button></div>').join('') : '<p class="muted">Chưa có thay đổi nào.</p>') +
        '<button class="ghost gap" style="width:100%" data-act="closeModal">Đóng</button>');
    } catch (e) { toast(errText(e), true); }
  }

  async function loadAdmin(keepFocus) {
    const a = S.admin;
    try {
      const paths = {
        reports: '/api/admin/reports?range=' + a.repRange,
        products: '/api/admin/products?page=' + a.page + '&filter=' + encodeURIComponent(a.filter) + '&search=' + encodeURIComponent(a.search),
        orders: '/api/admin/orders?range=' + a.range, users: '/api/admin/users', settings: '/api/settings',
      };
      a.data = await api('GET', paths[a.tab]);
      if (S.screen === 'admin' && !(keepFocus && document.activeElement && document.activeElement.id === 'pSearch')) render();
      clearTimers();
      if (a.tab === 'reports') timers.push(setInterval(() => { if (S.screen === 'admin' && a.tab === 'reports') loadAdmin(); }, 15000));
    } catch (e) { toast(errText(e), true); }
  }

  // ---------------------------------------------------------------- sự kiện toàn cục
  function bind() {
    document.addEventListener('click', (ev) => {
      const el = ev.target.closest && ev.target.closest('[data-act]'); if (!el) return;
      if (ev.target.closest('[data-stop]') && el.dataset.act === 'closeOverlay') return;
      const fn = A[el.dataset.act]; if (fn) fn(el, ev);
    });
    document.addEventListener('submit', (ev) => {
      if (ev.target.id !== 'loginForm') return; ev.preventDefault();
      const f = ev.target; doLogin(f.u.value, f.p.value);
    });
    document.addEventListener('change', (ev) => {
      const t = ev.target, k = t.dataset && t.dataset.in;
      if (k === 'price') {
        const v = parseMoney(t.value); if (v == null) return;
        run(async () => { S.order = await api('PATCH', '/api/orders/' + S.order.id + '/items/' + t.dataset.item, { manual_price: v }); render(); });
      } else if (k === 'histRange') { S.hist.range = t.value; A.toHistory(); }
      else if (k === 'admRange') { S.admin.range = t.value; loadAdmin(); }
      else if (k === 'repRange') { S.admin.repRange = t.value; loadAdmin(); }
      else if (t.id === 'sMinDef') { $('#sMinThr').disabled = t.checked; $('#sMinV').textContent = t.checked ? 'mặc định' : $('#sMinThr').value; }
      else if (t.id === 'sDef') { $('#sThr').disabled = t.checked; $('#sThrV').textContent = t.checked ? 'mặc định' : $('#sThr').value; }
    });
    document.addEventListener('input', (ev) => {
      const t = ev.target, k = t.dataset && t.dataset.in;
      if (k === 'sheetQ') { clearTimeout(searchTimer); if (S.sheet) S.sheet._q = t.value; searchTimer = setTimeout(() => searchProducts(t.value).catch(() => {}), 250); }
      else if (k === 'pSearch') { clearTimeout(searchTimer); searchTimer = setTimeout(() => { S.admin.search = t.value; S.admin.page = 1; loadAdmin(true); }, 350); }
      else if (k === 'thr') { $('#sThrV').textContent = t.value; }
      else if (k === 'minthr') { $('#sMinV').textContent = t.value; }
    });
    document.addEventListener('keydown', (ev) => {
      const t = ev.target, k = t.dataset && t.dataset.in;
      if (ev.key === 'Enter' && k === 'sheetBarcode') { ev.preventDefault(); return barcodeLookup(t.value, true); }
      if (ev.key === 'Enter' && k === 'price') { t.blur(); return; }
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || !S.token || !['viewfinder', 'invoice'].includes(S.screen)) return;
      // máy quét mã vạch USB/Bluetooth gõ như bàn phím: các phím cách nhau < 50ms rồi Enter
      const now = Date.now();
      if (ev.key === 'Enter') { if (scanBuf.length >= 6) barcodeLookup(scanBuf, false); scanBuf = ''; return; }
      if (ev.key.length === 1) { scanBuf = now - scanLast < 50 ? scanBuf + ev.key : ev.key; scanLast = now; }
    });
    document.addEventListener('change', (ev) => {
      if (ev.target.id === 'file' && ev.target.files && ev.target.files[0]) { submitPhoto(ev.target.files[0]); ev.target.value = ''; }
    });
  }

  async function enterPos() {
    const o = await resumeOrder();
    if (o && o.items.length) { go('invoice'); toast('Đã khôi phục đơn #' + o.id); } else go('viewfinder');
  }

  async function doLogin(u, p) {
    if (S.busy) return; S.busy = true;
    try {
      const r = await api('POST', '/api/auth/login', { username: u, password: p });
      S.token = r.token; S.user = r.user; S.loginMsg = ''; if (store) store.setItem('tok', r.token);
      S.settings = (await api('GET', '/api/settings')) || S.settings;
      if (r.user.role === 'admin') { go('admin'); loadAdmin(); }
      else if (!r.user.has_seen_onboarding) go('onboarding');
      else await enterPos();
    } catch (e) {
      S.loginMsg = e.code === 'RATE_LIMITED' ? ERR.RATE_LIMITED + ' (' + ((e.extra && e.extra.retry_after) || '?') + 's)' : errText(e);
      render();
    } finally { S.busy = false; }
  }

  async function init() {
    bind();
    const tok = store && store.getItem('tok');
    if (tok) {
      S.token = tok;
      try {
        const me = await api('GET', '/api/me'); S.user = me.user; S.settings = me.settings;
        if (me.user.role === 'admin') { S.screen = 'admin'; render(); loadAdmin(); }
        else if (!me.user.has_seen_onboarding) go('onboarding');
        else await enterPos();
        return;
      } catch (e) { S.token = null; if (store) store.removeItem('tok'); }
    }
    render();
  }

  // xuất ra cho test
  root.__pos = { esc, fmtMoney, dayKey, dayLabel, groupByDay, tiltAngle, parseMoney, uuid, S, api, A, ERR, resumeOrder, prepareImage, openHistory, itemRow, vInvoice, vPay, vHistory, vAdmin, aProducts, aSettings, aUsers, aOrders, aReports, vViewfinder, vDone, vLogin, vOnboarding, vLoading, submitPhoto, TILT_LIMIT };
  if (hasDom) { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init(); }
})(typeof window !== 'undefined' ? window : globalThis);
