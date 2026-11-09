(() => {
  const button = document.getElementById('publishLine');
  if (!button) return;
  const dialog = document.getElementById('linePublishDialog');
  const confirm = document.getElementById('linePublishConfirm');
  const cancel = document.getElementById('linePublishCancel');
  const error = document.getElementById('linePublishError');
  const status = document.getElementById('linePublishStatus');
  const image = document.getElementById('linePublishImage');
  let preview = null;
  let sending = false;
  const dateLabel = value => value.split('-').reverse().join('/');

  async function loadPreview() {
    const response = await fetch('/line/publish-preview?week=' + encodeURIComponent(button.dataset.week));
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'Không tải được bản xem trước.');
    return data;
  }

  loadPreview().then(data => {
    if (data.has_previous) button.textContent = 'Gửi lịch cập nhật';
  }).catch(() => {});

  button.addEventListener('click', async () => {
    if (sending) return;
    button.disabled = true;
    status.textContent = 'Đang tải bản xem trước…';
    try {
      preview = await loadPreview();
      if (!preview.shifts) throw new Error('Tuần đang xem chưa có ca làm để chốt.');
      if (preview.already_sent) {
        status.textContent = 'Phiên bản lịch này đã được LINE tiếp nhận. Khi sửa lịch, bạn có thể gửi bản cập nhật.';
        return;
      }
      const label = preview.has_previous ? 'Gửi lịch cập nhật' : 'Chốt lịch & gửi LINE';
      document.getElementById('linePublishTitle').textContent = label;
      confirm.textContent = label;
      document.getElementById('linePublishSummary').textContent =
        `Tuần ${dateLabel(preview.week)}–${dateLabel(preview.end)} · Nhóm nhận: ${preview.group_name} · ${preview.shifts} ca làm.`;
      document.getElementById('linePublishWishNote').textContent = preview.wishes
        ? `Có ${preview.wishes} ca 希望 trên bảng. Khi chốt, các ca này sẽ được xác nhận thành ca làm chính thức theo ảnh bên dưới.`
        : 'Các ca đã xếp sẽ được chốt và gửi vào nhóm.';
      error.textContent = '';
      image.src = '/line/publish-preview.png?week=' + encodeURIComponent(preview.week) + '&version=' + encodeURIComponent(preview.version);
      image.onerror = () => {
        error.textContent = 'Không tải được ảnh hoặc lịch vừa thay đổi. Đóng bảng này và mở lại bản xem trước.';
        confirm.disabled = true;
      };
      confirm.disabled = true;
      image.onload = () => { confirm.disabled = false; };
      dialog.showModal();
      status.textContent = '';
    } catch (problem) {
      status.textContent = problem.message || 'Không kết nối được máy chủ. Vui lòng thử lại.';
    } finally {
      button.disabled = false;
    }
  });

  cancel.addEventListener('click', () => { if (!sending) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (sending) event.preventDefault(); });
  confirm.addEventListener('click', async () => {
    if (!preview || sending) return;
    sending = true;
    confirm.disabled = cancel.disabled = button.disabled = true;
    const originalLabel = confirm.textContent;
    confirm.textContent = 'Đang gửi…';
    error.textContent = '';
    try {
      const response = await fetch('/line/publish', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({week: preview.week, version: preview.version})
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Chưa gửi được lịch. Vui lòng thử lại.');
      status.textContent = data.message;
      button.textContent = 'Gửi lịch cập nhật';
      dialog.close();
      // Reflect wish approval on the board without altering any dates or shifts.
      document.querySelectorAll('.shift.kibo-shift').forEach(cell => {
        cell.classList.remove('kibo-shift', 'kibo-overlap');
        if (cell.title.startsWith('Buổi tối có ')) cell.removeAttribute('title');
      });
      document.querySelectorAll('.kibo-tag').forEach(tag => tag.remove());
    } catch (problem) {
      error.textContent = problem.message || 'Không kết nối được máy chủ. Bấm gửi lại để thử cùng phiên bản.';
    } finally {
      sending = false;
      confirm.textContent = originalLabel;
      confirm.disabled = cancel.disabled = button.disabled = false;
    }
  });
})();
