// Smoke test giao diện: chạy app.js với DOM giả, gọi API THẬT của backend đang chạy.
//   node tests/frontend_smoke.js <base_url> <staff_pw> <admin_pw> <photo.jpg>
const fs = require('fs'), path = require('path');
const [base, STAFF_PW, ADMIN_PW, PHOTO, ADV_PW] = process.argv.slice(2);
const els = {}, handlers = {};
const mk = (sel) => els[sel] || (els[sel] = { sel, innerHTML: '', textContent: '', className: '', value: '', checked: false, disabled: false, dataset: {},
  classList: { add() {}, remove() {} }, addEventListener() {}, click() {}, blur() {}, focus() {}, scrollIntoView() {}, closest() { return null; } });
global.document = { readyState: 'complete', activeElement: null,
  querySelector: (s) => mk(s), createElement: () => ({ getContext: () => ({ drawImage() {} }), toBlob() {} }),
  addEventListener: (t, f) => { (handlers[t] = handlers[t] || []).push(f); } };
const store = {}; global.sessionStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
const realFetch = fetch; global.fetch = (u, o) => realFetch(base + u, o);
global.confirm = () => { throw new Error('không được dùng confirm() của trình duyệt'); }; global.prompt = () => { throw new Error('không được dùng prompt() của trình duyệt'); };
global.print = () => {};
// Hộp thoại của app (thay confirm/prompt): mặc định tự bấm đồng ý; đặt autoDlg = false để tự điều khiển.
let autoDlg = true;
const dlgOpen = () => (els['#modal-root'] ? els['#modal-root'].innerHTML : '').includes('data-act="dlgOk"');
setInterval(() => { if (autoDlg && dlgOpen()) { mk('#dlgPw').value = 'newpass123'; mk('#dlgPw2').value = 'newpass123'; global.__pos.A.dlgOk(); } }, 20).unref();
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
  ok((await P.api('GET', '/api/me')).user.has_seen_onboarding === true, 'hướng dẫn được ghi nhận ngay khi hiện (chưa bấm Tiếp/Bỏ qua) -> lần sau không hiện lại');
  for (let i = 0; i < 4; i++) A.onbNext(); await sleep(300);
  ok(/<input type="file" id="file" accept="image\/\*" class/.test(html()) && html().includes('id="fileCam" accept="image/*" capture="environment"'),
    '"Chọn ảnh" không ép mở camera (không có capture); ô chụp bằng camera máy tách riêng');
  ok(P.camFailText('insecure').includes('http://') && P.camFailText('denied').includes('quyền camera'), 'lý do không mở được camera nêu đúng nguyên nhân (http / chưa cấp quyền)');
  ok(S.screen === 'viewfinder' && html().includes('data-act="shoot"'), 'vào màn chụp; không có camera -> vẫn dựng được (fallback chọn ảnh)');
  // chụp bằng ảnh thật
  const buf = fs.readFileSync(PHOTO);
  const p = P.submitPhoto(new Blob([buf], { type: 'image/jpeg' }));
  await sleep(100); ok(S.screen === 'loading' && html().includes('Đang nhận diện'), 'màn loading (skeleton)');
  await p; await sleep(200);
  { // admin đang áp dụng thiết lập nâng cao -> màn chờ báo rõ thay cho "lâu hơn bình thường"
    const keep = S.job; S.job = { pos: 0, slow: true, reloading: true };
    ok(P.vLoading().includes('đang nạp lại thiết lập') && !P.vLoading().includes('lâu hơn bình thường'), 'màn loading báo hệ thống đang nạp lại thiết lập');
    S.job = { pos: 0, slow: true, reloading: false };
    ok(!P.vLoading().includes('nạp lại') && P.vLoading().includes('lâu hơn bình thường'), 'màn loading bình thường không báo nạp lại');
    S.job = keep;
  }
  ok(S.screen === 'invoice' && S.order.items.length > 0, 'nhận diện xong -> hoá đơn (' + S.order.items.length + ' dòng, ' + S.order.item_count + ' sản phẩm)');
  { // ảnh kết quả + bbox: xanh/vàng theo trạng thái dòng; chạm dòng/khung -> đánh dấu; chạm ảnh -> phóng to
    const cap = (S.order.captures || [])[0];
    ok(cap && cap.boxes.length >= 1 && html().includes('class="cap"') && html().includes('class="bx '), 'hoá đơn có ảnh vừa chụp + ' + (cap ? cap.boxes.length : 0) + ' khung bbox');
    const ci = await realFetch(base + cap.image_url); ok(ci.status === 200, 'ảnh gốc lượt chụp tải được qua URL ký');
    const unc = S.order.items.find((i) => i.flagged), acc = S.order.items.find((i) => !i.flagged);
    ok(!unc || html().includes('bx unc'), 'dòng cần xác nhận -> khung vàng');
    const rejN = cap.boxes.filter((b) => b.status === 'rejected').length;
    ok(!rejN || (html().includes('bx rej') && html().includes('chưa nhận diện') && html().includes('data-act="openAdd"')), 'vật chưa nhận diện -> khung đỏ (' + rejN + '), chạm để thêm thủ công');
    if (acc) { click('focusItem', { item: String(acc.id) }); ok(S.focusItem === acc.id && html().includes('bx ok hl') && html().includes('bx ') && html().includes(' dim'), 'chạm dòng -> khung của dòng đó sáng, khung khác mờ');
      click('focusItem', { item: String(acc.id) }); ok(S.focusItem === null, 'chạm lại -> bỏ đánh dấu'); }
    const b0 = cap.boxes[0]; click('focusBox', { item: String(b0.item_id) }); ok(S.focusItem === b0.item_id, 'chạm khung -> đánh dấu dòng tương ứng');
    click('focusBox', { item: String(b0.item_id) });
    click('zoomCap'); ok(els['#modal-root'].innerHTML.includes('zoombox') && els['#modal-root'].innerHTML.includes('1×'), 'chạm ảnh -> mở phóng to');
    click('zoomStep', { d: '1' }); ok(S.zoom === 2 && els['#modal-root'].innerHTML.includes('width:200%'), 'phóng to 2×');
    click('closeModal'); ok(!S.zoomOpen, 'đóng phóng to'); }
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
  ok(html().includes('thiếu màu tham chiếu') && html().includes('data-act="evidence"') && html().includes('Chưa đặt tên ('), 'tab sản phẩm: badge thiếu màu tham chiếu + nút bằng chứng + lọc chưa đặt tên');
  const target = S.admin.data.items.find((x) => !x.barcode);
  mk('[data-f="price"][data-pid="' + target.id + '"]').value = '15.500'; mk('[data-f="barcode"][data-pid="' + target.id + '"]').value = '';
  click('saveProduct', { pid: target.id }); await sleep(700);
  const chk = await (await realFetch(base + '/api/admin/products?search=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
  ok(chk.items.find((x) => x.id === target.id).price === 15500, 'admin sửa giá SKU ' + target.id + ' = 15.500đ');
  click('filter', { f: 'missing_price' }); await sleep(500); ok(S.admin.data.items.every((x) => x.price == null), 'lọc "thiếu giá" chỉ còn SKU chưa có giá');
  { const lg = await (await realFetch(base + '/api/admin/change-log?table=product&record=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
    A.history({ dataset: { pid: target.id } }); await sleep(500);
    ok(els['#modal-root'].innerHTML.includes('Hoàn tác') && els['#modal-root'].innerHTML.includes('15.500'), 'modal lịch sử thay đổi hiển thị giá cũ → mới');
    { // hộp thoại xác nhận của app: "Không" -> không làm gì và trả lại modal đang mở
      autoDlg = false; const before = els['#modal-root'].innerHTML;
      click('revert', { id: String(lg.items[0].id), pid: target.id }); await sleep(100);
      ok(dlgOpen() && els['#modal-root'].innerHTML.includes('Hoàn tác thay đổi này?'), 'hoàn tác -> hộp thoại xác nhận của app (không dùng confirm của trình duyệt)');
      click('dlgCancel'); await sleep(300);
      const still = await (await realFetch(base + '/api/admin/products?search=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
      ok(els['#modal-root'].innerHTML === before && still.items.find((x) => x.id === target.id).price === 15500, 'bấm "Không" -> giữ nguyên giá, trả lại modal lịch sử');
      A.resetPw({ dataset: { id: '0', name: 'thu-ngan' } }); await sleep(100);
      const dh = els['#modal-root'].innerHTML;
      ok(dh.includes('thu-ngan') && dh.includes('id="dlgPw" type="password"') && dh.includes('id="dlgPw2" type="password"'), 'đặt lại mật khẩu: hộp thoại riêng, ô mật khẩu ẩn ký tự, gõ hai lần');
      mk('#dlgPw').value = 'ngan'; mk('#dlgPw2').value = 'ngan'; click('dlgOk');
      ok(dlgOpen() && els['#dlgErr'].textContent.includes('8 ký tự'), 'mật khẩu ngắn -> báo lỗi, chưa gửi');
      mk('#dlgPw').value = 'matkhau-moi-1'; mk('#dlgPw2').value = 'matkhau-moi-2'; click('dlgOk');
      ok(dlgOpen() && els['#dlgErr'].textContent.includes('không khớp'), 'hai lần nhập khác nhau -> báo lỗi, chưa gửi');
      click('dlgCancel'); await sleep(100); ok(els['#modal-root'].innerHTML === before, 'huỷ đặt lại mật khẩu -> không gửi gì');
      autoDlg = true;
    }
    click('revert', { id: String(lg.items[0].id), pid: target.id }); await sleep(800);
    const back = await (await realFetch(base + '/api/admin/products?search=' + target.id, { headers: { Authorization: 'Bearer ' + S.token } })).json();
    ok(back.items.find((x) => x.id === target.id).price === null, 'hoàn tác giá -> trở về "chưa có giá"'); }
  { // bằng chứng nhận diện: phải tick xác nhận; lưu xong API trả đúng giá trị
    A.evidence({ dataset: { pid: target.id } }); await sleep(500);
    ok(els['#modal-root'].innerHTML.includes('Bằng chứng nhận diện') && els['#modal-root'].innerHTML.includes('Tôi hiểu'), 'modal bằng chứng nhận diện có ô xác nhận');
    mk('#evOcr').value = 'testkw, TESTKW'; mk('#evColor').value = ''; mk('#evConf').value = ''; mk('#evHex').value = '';
    mk('#evConfirm').checked = false; click('saveEvidence', { pid: target.id }); await sleep(400);
    const H = { headers: { Authorization: 'Bearer ' + S.token } };
    let ev = await (await realFetch(base + '/api/admin/products/' + target.id + '/evidence', H)).json();
    ok(!(ev.evidence.ocr_keywords || []).includes('TESTKW'), 'chưa tick xác nhận -> không lưu bằng chứng');
    mk('#evConfirm').checked = true; click('saveEvidence', { pid: target.id }); await sleep(700);
    ev = await (await realFetch(base + '/api/admin/products/' + target.id + '/evidence', H)).json();
    ok(JSON.stringify(ev.evidence.ocr_keywords) === '["TESTKW"]', 'lưu bằng chứng: từ khoá OCR được chuẩn hoá (chữ hoa, bỏ trùng)');
    A.history({ dataset: { pid: target.id } }); await sleep(500);
    ok(els['#modal-root'].innerHTML.includes('Từ khoá OCR'), 'lịch sử thay đổi có dòng bằng chứng'); }
  { // gợi ý chuẩn hoá + dải ảnh gallery (data_demo có gallery) + thử bằng chứng
    A.evidence({ dataset: { pid: target.id } }); await sleep(600);
    const m = els['#modal-root'].innerHTML;
    ok(m.includes('BE-203') && m.includes('BE203'), 'modal bằng chứng có gợi ý chuẩn hoá từ khoá');
    ok(m.includes('data-act="pickColor"') && S.evGallery.length >= 1, 'modal bằng chứng có ' + S.evGallery.length + ' ảnh gallery để chấm màu');
    const g = await realFetch(base + S.evGallery[0]); ok(g.status === 200 && (g.headers.get('content-type') || '').includes('jpeg'), 'ảnh gallery tải được qua URL ký');
    mk('#evOcr').value = 'abc-12345'; A.pickColor({ dataset: { i: '0', pid: target.id } }); await sleep(300);
    ok(els['#modal-root'].innerHTML.includes('Chạm vào vùng màu') && S.evPick.draft.ocr_keywords[0] === 'abc-12345', 'chấm màu: mở ảnh, giữ bản nháp đang gõ');
    S.evPick.hex = '#123456'; click('pickUse'); await sleep(600);
    ok(els['#modal-root'].innerHTML.includes('#123456') && els['#modal-root'].innerHTML.includes('abc-12345'), 'dùng màu đã chấm -> quay lại modal, điền hex, giữ từ khoá');
    click('closeModal');
    const tr = await (await realFetch(base + '/api/admin/evidence-test', { method: 'POST', headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'image/jpeg' }, body: buf })).json();
    ok(Array.isArray(tr.items) && tr.items.length >= 1, 'thử bằng chứng trả ' + (tr.items || []).length + ' vật'); }
  if (ADV_PW) { // thiết lập nâng cao: chỉ xem / cần áp dụng; áp dụng cần mật khẩu nâng cao; hoàn tác
    click('tab', { t: 'advanced' }); await sleep(600);
    ok(html().includes('Nâng cao') && html().includes('cần áp dụng') && html().includes('chỉ xem'), 'tab Nâng cao: phân mức chỉ xem / cần áp dụng');
    const items = S.admin.data.items, ti = items.findIndex((x) => x.key === 'retrieval.top_k');
    items.forEach((it, i) => { if (it.tier === 'reload') { mk('#cfg' + i).value = String(it.value); mk('#cfg' + i).checked = it.value === true; } });
    mk('#cfg' + ti).value = String(items[ti].value + 1);
    click('cfgApply'); ok(els['#modal-root'].innerHTML.includes('Áp dụng thiết lập nâng cao') && els['#modal-root'].innerHTML.includes('Số ứng viên'), 'áp dụng -> hộp xác nhận liệt kê đúng 1 thay đổi');
    mk('#advPw').value = 'sai-mat-khau'; mk('#cfgConfirm').checked = true; click('cfgConfirm'); await sleep(600);
    let cfg = await (await realFetch(base + '/api/admin/config', { headers: { Authorization: 'Bearer ' + S.token } })).json();
    ok(!cfg.items.find((x) => x.key === 'retrieval.top_k').overridden, 'sai mật khẩu nâng cao -> không áp dụng');
    click('cfgApply'); mk('#advPw').value = ADV_PW; mk('#cfgConfirm').checked = true; click('cfgConfirm'); await sleep(900);
    cfg = await (await realFetch(base + '/api/admin/config', { headers: { Authorization: 'Bearer ' + S.token } })).json();
    const top = cfg.items.find((x) => x.key === 'retrieval.top_k');
    ok(top.overridden && top.value === items[ti].value + 1, 'đúng mật khẩu nâng cao -> áp dụng, top_k = ' + top.value);
    const lg = await (await realFetch(base + '/api/admin/change-log?table=config', { headers: { Authorization: 'Bearer ' + S.token } })).json();
    await A.cfgHistory(); mk('#advPwH').value = ADV_PW; click('cfgRevert', { id: String(lg.items[0].id) }); await sleep(900);
    cfg = await (await realFetch(base + '/api/admin/config', { headers: { Authorization: 'Bearer ' + S.token } })).json();
    ok(!cfg.items.find((x) => x.key === 'retrieval.top_k').overridden, 'hoàn tác thiết lập nâng cao -> về giá trị gốc'); }
  if (ADV_PW) { // kiểm định: chặn chụp trong lúc chạy, hiện kết quả so baseline
    click('tab', { t: 'advanced' }); await sleep(600);
    ok(html().includes('Kiểm định độ chính xác'), 'tab Nâng cao có khối kiểm định');
    click('valStart'); mk('#valPw').value = ADV_PW; mk('#valConfirm').checked = true; click('valConfirm'); await sleep(300);
    let v = await (await realFetch(base + '/api/admin/validation', { headers: { Authorization: 'Bearer ' + S.token } })).json();
    ok(v.status === 'running' || v.status === 'done', 'bắt đầu kiểm định (' + v.status + ')');
    for (let k = 0; k < 40 && v.status === 'running'; k++) { await sleep(250); v = await (await realFetch(base + '/api/admin/validation', { headers: { Authorization: 'Bearer ' + S.token } })).json(); }
    ok(v.status === 'done' && v.result && v.result.f1 != null, 'kiểm định xong, F1 = ' + (v.result || {}).f1); }
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
