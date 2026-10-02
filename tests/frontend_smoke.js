// Smoke test giao diện: chạy app.js với DOM giả, gọi API THẬT của backend đang chạy.
//   node tests/frontend_smoke.js <base_url> <staff_pw> <admin_pw> <photo.jpg>
const fs = require('fs'), path = require('path');
const [base, STAFF_PW, ADMIN_PW, PHOTO] = process.argv.slice(2);
const els = {}, handlers = {};
const mk = (sel) => els[sel] || (els[sel] = { sel, innerHTML: '', textContent: '', className: '', value: '', checked: false, disabled: false, dataset: {},
  classList: { add() {}, remove() {} }, addEventListener() {}, click() {}, blur() {}, focus() {}, scrollIntoView() {}, closest() { return null; } });
global.document = { readyState: 'complete', activeElement: null,
  querySelector: (s) => mk(s), createElement: () => ({ getContext: () => ({ drawImage() {} }), toBlob() {} }),
  addEventListener: (t, f) => { (handlers[t] = handlers[t] || []).push(f); } };
const store = {}; global.sessionStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
const realFetch = fetch; global.fetch = (u, o) => realFetch(base + u, o);
global.confirm = () => true; global.prompt = () => 'newpass123'; global.print = () => {};
require(path.join(__dirname, '..', 'frontend', 'app.js'));
const P = global.__pos, S = P.S, A = P.A;
let fails = 0; const ok = (c, m) => { console.log((c ? 'OK   ' : 'FAIL ') + m); if (!c) fails++; };
const html = () => els['#app'].innerHTML;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fire = (type, ev) => (handlers[type] || []).forEach((f) => f(ev));
const click = (act, ds = {}) => { const el = { dataset: Object.assign({ act }, ds), closest() { return null; } }; A[act](el, { stopPropagation() {}, target: el }); };

(async () => {
  await sleep(200);
  ok(S.screen === 'login' && html().includes('Vào ca làm việc'), 'màn đăng nhập hiển thị');
  // sai mật khẩu
  fire('submit', { target: { id: 'loginForm', u: { value: 'staff' }, p: { value: 'sai' } }, preventDefault() {} }); await sleep(600);
  ok(S.screen === 'login' && html().includes('Sai tài khoản'), 'sai mật khẩu -> thông báo, ở lại màn đăng nhập');
  // đăng nhập staff
  fire('submit', { target: { id: 'loginForm', u: { value: 'staff' }, p: { value: STAFF_PW } }, preventDefault() {} }); await sleep(800);
  ok(S.screen === 'onboarding' && S.user.role === 'staff', 'staff lần đầu -> onboarding');
  ok(!html().includes('data-act="toAdmin"') , 'staff không thấy nút quản trị');
  for (let i = 0; i < 4; i++) A.onbNext(); await sleep(300);
  ok(S.screen === 'viewfinder' && html().includes('data-act="shoot"'), 'vào màn chụp; không có camera -> vẫn dựng được (fallback chọn ảnh)');
  // chụp bằng ảnh thật
  const buf = fs.readFileSync(PHOTO);
  const p = P.submitPhoto(new Blob([buf], { type: 'image/jpeg' }));
  await sleep(100); ok(S.screen === 'loading' && html().includes('Đang nhận diện'), 'màn loading (skeleton)');
  await p; await sleep(200);
  ok(S.screen === 'invoice' && S.order.items.length > 0, 'nhận diện xong -> hoá đơn (' + S.order.items.length + ' dòng, ' + S.order.item_count + ' sản phẩm)');
  { const id0 = S.order.id, n0 = S.order.items.length; S.order = null; const r = await P.resumeOrder();
    ok(r && r.id === id0 && r.items.length === n0, 'khôi phục đơn đang mở sau khi "tải lại trang" (đơn #' + id0 + ')');
    const raw = new Blob([buf]); ok((await P.prepareImage(raw)) === raw, 'prepareImage: không có canvas -> giữ nguyên ảnh (không làm hỏng luồng)'); }
  ok(typeof S.unrec === 'number' && (S.unrec > 0) === html().includes('vật chưa nhận diện được'), 'banner "vật chưa nhận diện được" khớp cảnh báo của pipeline (unrec=' + S.unrec + ')');
  if (S.unrec) { A.dismissUnrec(); ok(!html().includes('vật chưa nhận diện được'), 'bấm "Bỏ qua" -> ẩn banner vật chưa nhận diện'); }
  ok(typeof S.overlap === 'boolean' && S.overlap === html().includes('chồng lên nhau'), 'banner chồng lấp khớp cảnh báo của pipeline (overlap=' + S.overlap + ')');
  if (S.overlap) { A.dismissOverlap(); ok(!html().includes('chồng lên nhau'), 'bấm "Bỏ qua" -> ẩn banner'); }
  const flagged = S.order.items.filter((i) => i.flagged);
  ok(flagged.length >= 1 && html().includes('⚠') && html().includes('class="item flag"'), 'dòng uncertain có viền vàng + icon cảnh báo');
  ok(S.order.items.every((i) => i.thumbnail_url), 'mọi dòng có ảnh crop');
  const t = await realFetch(base + S.order.items[0].thumbnail_url); ok(t.status === 200 && (await t.arrayBuffer()).byteLength > 200, 'ảnh crop tải được qua URL ký');
  ok(html().includes('class="pin need"') && html().includes('Nhập giá cho'), 'SKU thiếu giá -> ô nhập giá tay, nút thanh toán yêu cầu nhập giá');
  A.toPay({}); ok(S.screen === 'invoice', 'bấm thanh toán khi còn thiếu giá -> bị chặn, ở lại hoá đơn');
  // xác nhận dòng flagged
  click('openFix', { item: String(flagged[0].id) }); await sleep(400);
  ok(S.sheet && S.sheet.results.length > 0 && els['#modal-root'].innerHTML.includes('Đúng, giữ nguyên'), 'bottom sheet xác nhận: có gợi ý + danh sách sản phẩm');
  click('confirmItem', { item: String(flagged[0].id) }); await sleep(400);
  ok(S.order.flagged_count === flagged.length - 1, 'xác nhận dòng -> hết cờ vàng');
  // tìm theo tên + thêm thủ công
  click('openAdd'); await sleep(300);
  const before = S.order.item_count; const first = S.sheet.results[0];
  click('pick', { id: first.id }); await sleep(400);
  ok(S.order.item_count === before + 1, 'thêm sản phẩm thủ công từ danh sách: ' + first.name);
  // ---- máy quét mã vạch (gõ nhanh + Enter) thêm sản phẩm vào đơn
  const H = { Authorization: 'Bearer ' + S.token };
  const cat = (await (await realFetch(base + '/api/catalog/products?search=', { headers: H })).json()).items;
  const withBc = cat.find((x) => x.barcode);
  const beforeBc = S.order.item_count;
  for (const ch of withBc.barcode) fire('keydown', { key: ch, target: { tagName: 'BODY', dataset: {} } });
  fire('keydown', { key: 'Enter', target: { tagName: 'BODY', dataset: {} } }); await sleep(700);
  ok(S.order.item_count === beforeBc + 1, 'quét mã vạch ' + withBc.barcode + ' -> thêm "' + withBc.name + '"');
  // ---- admin (qua API) đặt giá cho mọi SKU trong đơn, trừ một SKU để thử nhập giá tay
  const adminLogin = await (await realFetch(base + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: ADMIN_PW }) })).json();
  const AH = { Authorization: 'Bearer ' + adminLogin.token, 'Content-Type': 'application/json' };
  const pids = [...new Set(S.order.items.map((i) => i.product_id))];
  for (let i = 0; i < pids.length - 1; i++) await realFetch(base + '/api/admin/products/' + pids[i], { method: 'PATCH', headers: AH, body: JSON.stringify({ price: 12000 * (i + 1) }) });
  S.order = await P.api('GET', '/api/orders/' + S.order.id); click('toInvoice');
  const missing = S.order.items.filter((i) => i.price_missing);
  ok(missing.length >= 1 && html().includes('class="pin need"'), 'còn ' + missing.length + ' dòng thiếu giá -> ô nhập giá tay');
  const pinEl = { dataset: { in: 'price', item: String(missing[0].id) }, value: '12.500', id: '' };
  fire('change', { target: pinEl }); await sleep(500);
  ok(S.order.missing_price_count === 0 && S.order.items.find((i) => i.id === missing[0].id).unit_price === 12500, 'nhập giá tay "12.500" -> 12500đ, hết thiếu giá');
  ok(html().includes('Thanh toán</button>') && !html().includes('Nhập giá cho'), 'nút thanh toán mở khoá');
  // ---- thanh toán tiền mặt
  A.toPay({}); ok(S.screen === 'pay' && html().includes('Tiền mặt'), 'màn thanh toán');
  click('key', { k: '5' }); click('key', { k: '000' }); click('key', { k: '⌫' });
  ok(S.pay.given === 500, 'numpad: 5, 000, ⌫ -> 500');
  ok(html().includes('Còn thiếu') && html().includes('id="confirmBtn" disabled'), 'chưa đủ tiền -> nút xác nhận bị khoá');
  for (let i = 0; i < 8; i++) click('key', { k: '9' });
  ok(html().includes('Tiền thừa'), 'đủ tiền -> hiện tiền thừa');
  S.settings.auto_print_receipt = true; mk('#print-area').innerHTML = '';
  click('confirmPay'); await sleep(1200);
  ok(S.screen === 'done' && S.order.status === 'paid' && S.order.change_given === S.pay.given - S.order.total, 'thanh toán thành công, tiền thừa đúng (' + S.order.change_given + 'đ)');
  ok(html().includes('Thanh toán thành công'), 'màn hoàn tất');
  ok(els['#print-area'].innerHTML.includes('TỔNG'), 'bật auto_print_receipt -> tự in hoá đơn sau khi thanh toán'); S.settings.auto_print_receipt = false;
  A.print(); ok(els['#print-area'].innerHTML.includes('TỔNG'), 'hoá đơn in có tổng tiền');
  const paidId = S.order.id;
  // ---- lịch sử
  A.toHistory(); await sleep(500);
  ok(S.screen === 'history' && html().includes('#' + paidId) && html().includes('Hôm nay'), 'lịch sử có đơn vừa thu, nhóm "Hôm nay"');
  // ---- XSS: tên sản phẩm chứa HTML phải bị thoát
  ok(P.esc('<img src=x onerror=alert(1)>') === '&lt;img src=x onerror=alert(1)&gt;', 'esc() thoát HTML');
  A.newAfterDone(); ok(S.order === null && S.screen === 'viewfinder', 'đơn mới');
  S.settings.tilt_block_capture = true; S.tilt = 40; mk('#toast').textContent = ''; await A.shoot();
  ok(els['#toast'].textContent.includes('nghiêng'), 'bật tilt_block_capture + máy nghiêng 40° -> chặn chụp'); S.settings.tilt_block_capture = false; S.tilt = null;
  await A.logout(); ok(S.screen === 'login' && !S.token, 'đăng xuất');
  // ---- admin qua giao diện
  fire('submit', { target: { id: 'loginForm', u: { value: 'admin' }, p: { value: ADMIN_PW } }, preventDefault() {} }); await sleep(1000);
  ok(S.screen === 'admin' && html().includes('Báo cáo hôm nay') && html().includes('Doanh thu'), 'admin -> báo cáo');
  ok(html().includes('class="bar"') && html().includes('Top sản phẩm bán chạy'), 'báo cáo có biểu đồ doanh thu theo ngày và top sản phẩm');
  fire('change', { target: { dataset: { in: 'repRange' }, value: '30d', id: '' } }); await sleep(500);
  ok(S.admin.data.daily.length === 30 && S.admin.repRange === '30d', 'đổi khoảng báo cáo sang 30 ngày');
  ok(S.admin.data.orders_today >= 1 && S.admin.data.revenue_today > 0, 'báo cáo tính đơn vừa thu: ' + S.admin.data.orders_today + ' đơn, ' + S.admin.data.revenue_today + 'đ');
  click('tab', { t: 'products' }); await sleep(600);
  ok(html().includes('data-act="saveProduct"') && html().includes('Thiếu giá ('), 'tab sản phẩm: bảng + bộ lọc thiếu giá');
  const target = S.admin.data.items.find((x) => !x.barcode);
  mk('[data-f="price"][data-pid="' + target.id + '"]').value = '15.500'; mk('[data-f="barcode"][data-pid="' + target.id + '"]').value = '';
  click('saveProduct', { pid: target.id }); await sleep(700);
  const chk = await (await realFetch(base + '/api/admin/products?search=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
  ok(chk.items.find((x) => x.id === target.id).price === 15500, 'admin sửa giá SKU ' + target.id + ' = 15.500đ');
  click('filter', { f: 'missing_price' }); await sleep(500); ok(S.admin.data.items.every((x) => x.price == null), 'lọc "thiếu giá" chỉ còn SKU chưa có giá');
  { const lg = await (await realFetch(base + '/api/admin/change-log?table=product&record=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
    A.history({ dataset: { pid: target.id } }); await sleep(500);
    ok(els['#modal-root'].innerHTML.includes('Hoàn tác') && els['#modal-root'].innerHTML.includes('15.500'), 'modal lịch sử thay đổi hiển thị giá cũ → mới');
    click('revert', { id: String(lg.items[0].id), pid: target.id }); await sleep(800);
    const back = await (await realFetch(base + '/api/admin/products?search=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
    ok(back.items.find((x) => x.id === target.id).price === null, 'hoàn tác giá -> trở về "chưa có giá"'); }
  click('tab', { t: 'users' }); await sleep(500);
  mk('#nu').value = 'thungan2'; mk('#nn').value = 'Thu Ngan 2'; mk('#np').value = 'matkhau123'; mk('#nr').value = 'staff';
  click('createUser'); await sleep(700); ok(html().includes('thungan2'), 'admin thêm nhân viên mới');
  click('tab', { t: 'orders' }); await sleep(500); ok(html().includes('#' + paidId), 'tab đơn hàng liệt kê đơn ' + paidId);
  click('tab', { t: 'settings' }); await sleep(500);
  mk('#sAllow').checked = true; mk('#sDef').checked = false; mk('#sThr').value = '0.7'; mk('#sMinDef').checked = false; mk('#sMinThr').value = '0.55'; mk('#sTilt').checked = true; mk('#sPrint').checked = true; click('saveSettings'); await sleep(600);
  const st = await (await realFetch(base + '/api/settings', { headers: { Authorization: 'Bearer ' + S.token } })).json();
  ok(st.allow_checkout_without_price === true && st.similarity_threshold === 0.7 && st.min_confidence_accept === 0.55 && st.tilt_block_capture === true && st.auto_print_receipt === true, 'admin lưu cài đặt (thiếu giá, similarity 0.7, min_confidence 0.55, chặn nghiêng, tự in)');
  // ---- bị đá phiên khi đăng nhập nơi khác
  await realFetch(base + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: ADMIN_PW }) });
  try { await P.api('GET', '/api/me'); } catch (e) { /* mong đợi 401 */ }
  ok(S.screen === 'login' && S.loginMsg.includes('thiết bị khác'), 'đăng nhập nơi khác -> về màn đăng nhập kèm thông báo rõ');
  // ---- hàm thuần
  ok(P.fmtMoney(1234567) === '1.234.567đ' && P.fmtMoney(null) === '—', 'fmtMoney');
  ok(P.parseMoney('1.250.000đ') === 1250000 && P.parseMoney('') === null, 'parseMoney');
  ok(Math.round(P.tiltAngle(3, 4)) === 5, 'tiltAngle');
  const now = new Date(2026, 8, 30, 12), y = new Date(2026, 8, 29, 9);
  const g = P.groupByDay([{ created_at: now.toISOString(), status: 'paid', total: 100 }, { created_at: now.toISOString(), status: 'void', total: 50 }, { created_at: y.toISOString(), status: 'paid', total: 7 }], now);
  ok(g.length === 2 && g[0].label === 'Hôm nay' && g[0].total === 100 && g[0].count === 1 && g[1].label === 'Hôm qua', 'groupByDay: chỉ tính đơn đã thu, nhãn Hôm nay/Hôm qua');
  console.log(fails ? '\n' + fails + ' LỖI' : '\nTẤT CẢ ĐẠT'); process.exit(fails ? 1 : 0);
})().catch((e) => { console.error("LỖI KHÔNG BẮT ĐƯỢC:", e); process.exit(2); });
