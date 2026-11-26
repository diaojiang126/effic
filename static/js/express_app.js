const API_BASE = '/express/api';
const UPLOADS_BASE = '/express/uploads';

const uploadZone = document.getElementById('uploadZone');
const fileInput = document.getElementById('fileInput');
const batchFileInput = document.getElementById('batchFileInput');
const uploadLoading = document.getElementById('uploadLoading');
const resultCard = document.getElementById('resultCard');
const batchResultCard = document.getElementById('batchResultCard');

let currentResult = null;
let batchResults = [];
let batchCurrentIndex = 0;

// 结果表单任一字段变化时，实时同步到当前记录并刷新保存按钮的可点击状态
const resultContentEl = document.getElementById('resultContent');
if (resultContentEl) {
    ['input', 'change'].forEach(evt => {
        resultContentEl.addEventListener(evt, () => {
            syncBatchFormToCurrent();
            updateSaveButtonState();
        });
    });
}

uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('dragover');
});

uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFile(files[0]);
    }
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFile(e.target.files[0]);
    }
});

batchFileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleBatchFiles(e.target.files);
    }
});

function handleFile(file) {
    if (!file.type.startsWith('image/')) {
        showAlert('请上传图片文件', 'warning');
        return;
    }
    if (file.size > 16 * 1024 * 1024) {
        showAlert('文件大小不能超过16MB', 'warning');
        return;
    }

    uploadLoading.style.display = 'block';
    resultCard.style.display = 'none';
    currentResult = null;

    const formData = new FormData();
    formData.append('file', file);

    fetch(API_BASE + '/upload', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        uploadLoading.style.display = 'none';
        if (data.success) {
            // 单张与批量统一按「本次待保存批次」处理：识别后只生成草稿，
            // 必须全部核对并选好项目、类型后才能保存。
            batchResults = [data.data];
            batchCurrentIndex = 0;
            batchResultCard.style.display = 'none';
            displayBatchResult(0);
            loadRecords();
        } else {
            showAlert('识别失败: ' + (data.message || '未知错误'), 'danger');
        }
    })
    .catch(err => {
        uploadLoading.style.display = 'none';
        showAlert('上传失败: ' + err, 'danger');
    });
}

function handleBatchFiles(files) {
    const validFiles = Array.from(files).filter(f => {
        if (!f.type.startsWith('image/')) return false;
        if (f.size > 16 * 1024 * 1024) return false;
        return true;
    });

    if (validFiles.length === 0) {
        showAlert('请上传有效的图片文件', 'warning');
        return;
    }

    uploadLoading.style.display = 'block';
    batchResultCard.style.display = 'none';

    const formData = new FormData();
    validFiles.forEach(file => {
        formData.append('files', file);
    });

    fetch(API_BASE + '/upload/batch', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        uploadLoading.style.display = 'none';
        if (data.success) {
            document.getElementById('successCount').textContent = data.total;
            document.getElementById('failCount').textContent = data.failed;
            batchResultCard.style.display = 'block';
            loadRecords();
            if (data.data && data.data.length > 0) {
                batchResults = data.data;
                batchCurrentIndex = 0;
                displayBatchResult(0);
            }
        } else {
            showAlert('批量识别失败: ' + (data.message || '未知错误'), 'danger');
        }
    })
    .catch(err => {
        uploadLoading.style.display = 'none';
        showAlert('批量上传失败: ' + err, 'danger');
    });
}

function displayResult(data) {
    document.getElementById('resultImage').src = UPLOADS_BASE + '/' + data.image_path;
    document.getElementById('resultSender').value = data.sender_name || '';
    document.getElementById('resultSenderPhone').value = data.sender_phone || '';
    document.getElementById('resultSenderAddr').value = data.sender_address || '';
    document.getElementById('resultReceiver').value = data.receiver_name || '';
    document.getElementById('resultReceiverPhone').value = data.receiver_phone || '';
    document.getElementById('resultReceiverAddr').value = data.receiver_address || '';
    document.getElementById('resultItemName').value = data.item_name || '';
    document.getElementById('resultAmount').value = data.amount || '';
    document.getElementById('resultExpressNo').value = data.express_no || '';
    document.getElementById('resultProject').value = data.project || '';
    document.getElementById('resultType').value = data.record_type || '';

    loadProjectTypeOptions('result');

    resultCard.style.display = 'block';
    resultCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function displayBatchResult(index) {
    const r = batchResults[index];
    if (!r) return;
    currentResult = r;
    displayResult(r);

    document.getElementById('batchIndex').textContent = (batchCurrentIndex + 1) + ' / ' + batchResults.length;
    updateSaveButtonState();
}

/** 单条记录是否已完成核对：项目与类型都已填写 */
function isRecordComplete(record) {
    return !!(record
        && String(record.project || '').trim()
        && String(record.record_type || '').trim());
}

/**
 * 保存按钮门控：本次上传的所有文件都完成核对并选择项目、类型后，保存按钮才可点击。
 * 与后端 finalize 接口的校验保持一致，避免用户在信息不全时触发保存。
 */
function updateSaveButtonState() {
    const btn = document.getElementById('saveBtn');
    if (!btn) return;
    const hint = document.getElementById('saveHint');

    if (!batchResults.length) {
        btn.disabled = true;
        if (hint) hint.style.display = 'none';
        return;
    }

    const pending = batchResults.filter(r => !isRecordComplete(r)).length;
    btn.disabled = pending > 0;
    btn.innerHTML = pending > 0
        ? '<i class="fas fa-save"></i> 保存（待完成 ' + pending + '）'
        : '<i class="fas fa-save"></i> 保存（' + batchResults.length + '）';

    if (hint) {
        hint.textContent = pending > 0 ? '需全部文件完成核对并选择项目、类型后方可保存' : '';
        hint.style.display = pending > 0 ? 'inline' : 'none';
    }
}

/** 放弃本次上传：删除尚未保存的草稿记录（含图片）并清空界面 */
function discardUploadSession() {
    batchResults
        .filter(r => r && r.id)
        .forEach(r => {
            fetch(API_BASE + '/records/' + r.id, { method: 'DELETE' }).catch(() => {});
        });
    resetUploadSession();
}

function resetUploadSession() {
    currentResult = null;
    batchResults = [];
    batchCurrentIndex = 0;
    resultCard.style.display = 'none';
    batchResultCard.style.display = 'none';
    updateSaveButtonState();
}

function batchPrevRecord() {
    if (batchCurrentIndex > 0) {
        syncBatchFormToCurrent();
        batchCurrentIndex--;
        displayBatchResult(batchCurrentIndex);
    }
}

function batchNextRecord() {
    if (batchCurrentIndex < batchResults.length - 1) {
        syncBatchFormToCurrent();
        batchCurrentIndex++;
        displayBatchResult(batchCurrentIndex);
    }
}

function syncBatchFormToCurrent() {
    if (!batchResults[batchCurrentIndex]) return;
    const r = batchResults[batchCurrentIndex];
    r.sender_name = document.getElementById('resultSender').value;
    r.sender_phone = document.getElementById('resultSenderPhone').value;
    r.sender_address = document.getElementById('resultSenderAddr').value;
    r.receiver_name = document.getElementById('resultReceiver').value;
    r.receiver_phone = document.getElementById('resultReceiverPhone').value;
    r.receiver_address = document.getElementById('resultReceiverAddr').value;
    r.item_name = document.getElementById('resultItemName').value;
    r.amount = document.getElementById('resultAmount').value;
    r.express_no = document.getElementById('resultExpressNo').value;
    r.project = document.getElementById('resultProject').value;
    r.record_type = document.getElementById('resultType').value;
    if (currentResult) {
        Object.assign(currentResult, r);
    }
}

/** 保存：本次上传的全部文件都完成核对后，整体提交为已保存记录。 */
function saveResult() {
    if (!batchResults.length) {
        showAlert('没有可保存的记录', 'warning');
        return;
    }

    syncBatchFormToCurrent();

    const pending = batchResults.filter(r => !isRecordComplete(r)).length;
    if (pending > 0) {
        showAlert('还有 ' + pending + ' 个文件未选择项目或类型，请全部完成后再保存', 'warning');
        updateSaveButtonState();
        return;
    }

    const payload = batchResults.map(r => ({
        id: r.id,
        sender_name: r.sender_name || '',
        sender_phone: r.sender_phone || '',
        sender_address: r.sender_address || '',
        receiver_name: r.receiver_name || '',
        receiver_phone: r.receiver_phone || '',
        receiver_address: r.receiver_address || '',
        item_name: r.item_name || '',
        amount: r.amount || '',
        express_no: r.express_no || '',
        express_date: r.express_date || '',
        full_text: r.full_text || '',
        project: String(r.project || '').trim(),
        record_type: String(r.record_type || '').trim()
    }));

    const btn = document.getElementById('saveBtn');
    if (btn) btn.disabled = true;

    fetch(API_BASE + '/records/finalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ records: payload })
    })
    .then(res => res.json())
    .then(result => {
        if (result.success) {
            showAlert(result.message || '保存成功', 'success');
            resetUploadSession();
            loadRecords();
        } else {
            showAlert('保存失败: ' + (result.message || '未知错误'), 'danger');
            updateSaveButtonState();
        }
    })
    .catch(err => {
        showAlert('保存失败: ' + err, 'danger');
        updateSaveButtonState();
    });
}

/** 取消：放弃本次上传，未保存的草稿记录一并删除。 */
function cancelEdit() {
    const hasDrafts = batchResults.some(r => r && r.id);
    if (hasDrafts && !window.confirm('放弃后本次已上传的图片将被删除，确定继续？')) {
        return;
    }
    discardUploadSession();
}

function loadRecords() {
    fetch(API_BASE + '/records')
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                allRecords = data.data || [];
                renderTable(data.data);
            }
        })
        .catch(err => console.error('加载记录失败:', err));
}

function renderTable(records) {
    const tbody = document.getElementById('recordsTableBody');
    const noData = document.getElementById('noData');

    if (!records || records.length === 0) {
        tbody.innerHTML = '';
        noData.style.display = 'block';
        return;
    }

    noData.style.display = 'none';
    tbody.innerHTML = records.map(r => `
        <tr>
            <td>${r.id}</td>
            <td>${r.express_no || '-'}</td>
            <td>${r.sender_name || '-'}</td>
            <td>${r.receiver_name || '-'}</td>
            <td>${r.project || '-'}</td>
            <td>${r.record_type || '-'}</td>
            <td>${r.amount ? '¥' + r.amount : '-'}</td>
            <td class="text-nowrap">${r.created_at || '-'}</td>
            <td class="text-nowrap">
                <button class="btn btn-sm btn-primary me-1" onclick="viewDetail(${r.id})" title="查看详情">
                    <i class="fas fa-eye"></i>
                </button>
                <button class="btn btn-sm btn-success me-1" onclick="editRecord(${r.id})" title="编辑">
                    <i class="fas fa-edit"></i>
                </button>
                <button class="btn btn-sm btn-danger" onclick="deleteRecord(${r.id})" title="删除">
                    <i class="fas fa-trash"></i>
                </button>
            </td>
        </tr>
    `).join('');
}

function viewDetail(id) {
    fetch(API_BASE + '/records/' + id)
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                const r = data.data;
                const content = document.getElementById('detailContent');
                content.innerHTML = `
                    <div class="info-item">
                        <div class="label"><i class="fas fa-barcode"></i> 快递单号</div>
                        <div class="value">${r.express_no || '-'}</div>
                    </div>
                    <div class="row">
                        <div class="col-md-6">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-user"></i> 寄件人</div>
                                <div class="value">${r.sender_name || '-'}</div>
                            </div>
                            <div class="info-item">
                                <div class="label"><i class="fas fa-phone"></i> 寄件人电话</div>
                                <div class="value">${r.sender_phone || '-'}</div>
                            </div>
                            <div class="info-item">
                                <div class="label"><i class="fas fa-map-marker-alt"></i> 寄件人地址</div>
                                <div class="value">${r.sender_address || '-'}</div>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-user"></i> 收件人</div>
                                <div class="value">${r.receiver_name || '-'}</div>
                            </div>
                            <div class="info-item">
                                <div class="label"><i class="fas fa-phone"></i> 收件人电话</div>
                                <div class="value">${r.receiver_phone || '-'}</div>
                            </div>
                            <div class="info-item">
                                <div class="label"><i class="fas fa-map-marker-alt"></i> 收件人地址</div>
                                <div class="value">${r.receiver_address || '-'}</div>
                            </div>
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-md-4">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-box"></i> 物品名称</div>
                                <div class="value">${r.item_name || '-'}</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-tag"></i> 项目</div>
                                <div class="value">${r.project || '-'}</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-clipboard-list"></i> 类型</div>
                                <div class="value">${r.record_type || '-'}</div>
                            </div>
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-md-4">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-yen-sign"></i> 金额</div>
                                <div class="value">${r.amount ? '¥' + r.amount : '-'}</div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="info-item">
                                <div class="label"><i class="fas fa-calendar"></i> 创建时间</div>
                                <div class="value">${r.created_at || '-'}</div>
                            </div>
                        </div>
                    </div>
                `;

                const img = document.getElementById('detailImage');
                if (r.image_path) {
                    img.src = UPLOADS_BASE + '/' + r.image_path;
                    img.style.display = 'block';
                } else {
                    img.style.display = 'none';
                }

                new bootstrap.Modal(document.getElementById('detailModal')).show();
            }
        });
}

function editRecord(id) {
    fetch(API_BASE + '/records/' + id)
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                const r = data.data;
                document.getElementById('editId').value = r.id;
                document.getElementById('editSender').value = r.sender_name || '';
                document.getElementById('editSenderPhone').value = r.sender_phone || '';
                document.getElementById('editSenderAddr').value = r.sender_address || '';
                document.getElementById('editReceiver').value = r.receiver_name || '';
                document.getElementById('editReceiverPhone').value = r.receiver_phone || '';
                document.getElementById('editReceiverAddr').value = r.receiver_address || '';
                document.getElementById('editItemName').value = r.item_name || '';
                document.getElementById('editAmount').value = r.amount || '';
                document.getElementById('editExpressNo').value = r.express_no || '';
                document.getElementById('editProject').value = r.project || '';
                document.getElementById('editType').value = r.record_type || '';

                loadProjectTypeOptions('edit');

                new bootstrap.Modal(document.getElementById('editModal')).show();
            }
        });
}

function updateRecord() {
    const id = document.getElementById('editId').value;
    const project = document.getElementById('editProject').value.trim();
    const recordType = document.getElementById('editType').value.trim();

    if (!project) {
        showAlert('请填写项目', 'warning');
        document.getElementById('editProject').focus();
        return;
    }
    if (!recordType) {
        showAlert('请填写类型', 'warning');
        document.getElementById('editType').focus();
        return;
    }

    const data = {
        sender_name: document.getElementById('editSender').value,
        sender_phone: document.getElementById('editSenderPhone').value,
        sender_address: document.getElementById('editSenderAddr').value,
        receiver_name: document.getElementById('editReceiver').value,
        receiver_phone: document.getElementById('editReceiverPhone').value,
        receiver_address: document.getElementById('editReceiverAddr').value,
        item_name: document.getElementById('editItemName').value,
        amount: document.getElementById('editAmount').value,
        express_no: document.getElementById('editExpressNo').value,
        project: project,
        record_type: recordType
    };

    fetch(API_BASE + '/records/' + id, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(result => {
        if (result.success) {
            bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
            showAlert('更新成功', 'success');
            loadRecords();
        } else {
            showAlert('更新失败: ' + (result.message || '未知错误'), 'danger');
        }
    })
    .catch(err => {
        showAlert('更新失败: ' + err, 'danger');
    });
}

function deleteRecord(id) {
    if (!confirm('确定要删除这条记录吗？')) return;

    fetch(API_BASE + '/records/' + id, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                if (currentResult && currentResult.id === id) {
                    resetUploadSession();
                }
                showAlert('删除成功', 'success');
                loadRecords();
            } else {
                showAlert('删除失败: ' + (data.message || '未知错误'), 'danger');
            }
        });
}

// ===================== 导出（支持日期范围） =====================
let allRecords = [];

const EXPORT_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function formatDateValue(date) {
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${date.getFullYear()}-${month}-${day}`;
}

// 格式校验 + 真实性校验（可识别 2026-02-30 这类不存在的日期）
function isValidExportDate(value) {
    if (!EXPORT_DATE_RE.test(value)) return false;
    const parsed = new Date(value + 'T00:00:00');
    return !isNaN(parsed.getTime()) && formatDateValue(parsed) === value;
}

// 快捷范围：今天 / 昨天 / 本周 / 上月
function setExportRange(kind) {
    const today = new Date();
    let start = today;
    let end = today;
    if (kind === 'yesterday') {
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
        end = start;
    } else if (kind === 'week') {
        const offset = (today.getDay() + 6) % 7;   // 以周一为一周起点
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset);
    } else if (kind === 'lastMonth') {
        start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
        end = new Date(today.getFullYear(), today.getMonth(), 0);
    }
    document.getElementById('exportStartDate').value = formatDateValue(start);
    document.getElementById('exportEndDate').value = formatDateValue(end);
}

function clearExportRange() {
    document.getElementById('exportStartDate').value = '';
    document.getElementById('exportEndDate').value = '';
}

function readExportRange() {
    const start = document.getElementById('exportStartDate').value.trim();
    const end = document.getElementById('exportEndDate').value.trim();
    if (start && !isValidExportDate(start)) {
        showAlert('开始日期格式不正确，应为 YYYY-MM-DD', 'warning');
        return null;
    }
    if (end && !isValidExportDate(end)) {
        showAlert('结束日期格式不正确，应为 YYYY-MM-DD', 'warning');
        return null;
    }
    if (start && end && start > end) {
        showAlert('开始日期不能晚于结束日期', 'warning');
        return null;
    }
    return { start: start, end: end };
}

// 按「寄件日期」判断范围内是否有记录
function rangeHasRecord(range) {
    if (!range.start && !range.end) return true;
    if (!allRecords || allRecords.length === 0) return true;
    return allRecords.some(function (record) {
        const day = String(record.express_date || '').trim();
        if (!day) return false;
        if (range.start && day < range.start) return false;
        if (range.end && day > range.end) return false;
        return true;
    });
}

// 未识别到寄件日期的记录数：这些记录不会被任何日期范围命中
function countNoExpressDate() {
    if (!allRecords || allRecords.length === 0) return 0;
    return allRecords.filter(function (record) {
        return !String(record.express_date || '').trim();
    }).length;
}

function exportData(type) {
    const range = readExportRange();
    if (!range) return;
    // 仅做友好提示，不强行阻止（本地列表可能不是最新）
    if (range.start || range.end) {
        const warnings = [];
        if (!rangeHasRecord(range)) warnings.push('所选寄件日期范围内未找到记录');
        const missing = countNoExpressDate();
        if (missing > 0) warnings.push(`另有 ${missing} 条记录未识别到寄件日期，不会被包含`);
        if (warnings.length && !confirm(warnings.join('；') + '。仍要继续导出吗？')) {
            return;
        }
    }
    const params = new URLSearchParams();
    if (range.start) params.append('start_date', range.start);
    if (range.end) params.append('end_date', range.end);
    const query = params.toString();
    window.location.href = API_BASE + '/export/' + type + (query ? '?' + query : '');
}

function exportExcel() {
    exportData('excel');
}

function exportCsv() {
    exportData('csv');
}

function exportImages() {
    exportData('images');
}

document.addEventListener('DOMContentLoaded', () => {
    loadRecords();
});

function showAddOption(optionType, prefix) {
    document.getElementById('addOptionType').value = optionType;
    document.getElementById('addOptionPrefix').value = prefix;
    document.getElementById('addOptionValue').value = '';
    if (optionType === 'project') {
        document.getElementById('addOptionLabel').textContent = '项目名称';
    } else {
        document.getElementById('addOptionLabel').textContent = '类型名称';
    }
    new bootstrap.Modal(document.getElementById('addOptionModal')).show();
    setTimeout(() => document.getElementById('addOptionValue').focus(), 300);
}

function confirmAddOption() {
    const optionType = document.getElementById('addOptionType').value;
    const prefix = document.getElementById('addOptionPrefix').value;
    const value = document.getElementById('addOptionValue').value.trim();

    if (!value) {
        showAlert('请输入预设值名称', 'warning');
        document.getElementById('addOptionValue').focus();
        return;
    }

    fetch(API_BASE + '/options/' + optionType, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: value })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            bootstrap.Modal.getInstance(document.getElementById('addOptionModal')).hide();
            showAlert('预设值添加成功', 'success');
            const inputId = prefix === 'result'
                ? (optionType === 'project' ? 'resultProject' : 'resultType')
                : (optionType === 'project' ? 'editProject' : 'editType');
            // 选项列表是异步重建的，等重建完成后再选中新增值，并刷新保存按钮状态
            loadProjectTypeOptions(prefix).then(() => {
                document.getElementById(inputId).value = value;
                if (prefix === 'result') {
                    syncBatchFormToCurrent();
                    updateSaveButtonState();
                }
            });
        } else {
            showAlert('添加失败: ' + (data.message || '未知错误'), 'danger');
        }
    })
    .catch(err => {
        showAlert('添加失败: ' + err, 'danger');
    });
}

document.getElementById('addOptionValue').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        confirmAddOption();
    }
});

function loadProjectTypeOptions(prefix) {
    const projId = prefix === 'result' ? 'resultProject' : 'editProject';
    const typeId = prefix === 'result' ? 'resultType' : 'editType';
    const projectSelect = document.getElementById(projId);
    const typeSelect = document.getElementById(typeId);

    return Promise.all([
        fetch(API_BASE + '/options/project').then(r => r.json()),
        fetch(API_BASE + '/options/record_type').then(r => r.json())
    ]).then(([projectData, typeData]) => {
        if (projectData.success && projectData.data) {
            const currentVal = projectSelect.value;
            projectSelect.innerHTML = '<option value="">-- 请选择项目 --</option>'
                + projectData.data.map(v => `<option value="${v}">${v}</option>`).join('');
            projectSelect.value = currentVal;
        }
        if (typeData.success && typeData.data) {
            const currentVal = typeSelect.value;
            typeSelect.innerHTML = '<option value="">-- 请选择类型 --</option>'
                + typeData.data.map(v => `<option value="${v}">${v}</option>`).join('');
            typeSelect.value = currentVal;
        }
    }).catch(err => console.error('加载选项失败:', err));
}

function showDeleteByMonthModal() {
    const now = new Date();
    const currentYear = now.getFullYear();
    const currentMonth = now.getMonth() + 1;
    const yearSelect = document.getElementById('deleteYear');
    yearSelect.innerHTML = '';
    for (let y = currentYear; y >= 2020; y--) {
        const option = document.createElement('option');
        option.value = y;
        option.textContent = y + '年';
        yearSelect.appendChild(option);
    }
    yearSelect.value = currentYear;
    document.getElementById('deleteMonth').value = currentMonth;
    new bootstrap.Modal(document.getElementById('deleteByMonthModal')).show();
}

function deleteByMonth() {
    const year = document.getElementById('deleteYear').value;
    const month = document.getElementById('deleteMonth').value;
    const monthNames = ['', '1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
    if (!confirm('确定要清除 ' + year + '年' + monthNames[month] + ' 的所有快递单记录吗？\n此操作不可恢复！')) {
        return;
    }
    const btn = document.querySelector('#deleteByMonthModal .btn-danger');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 正在清除...';
    fetch(API_BASE + '/records/delete_by_month', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ year: parseInt(year), month: parseInt(month) })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            bootstrap.Modal.getInstance(document.getElementById('deleteByMonthModal')).hide();
            showAlert(data.message || '清除成功', 'success');
            loadRecords();
        } else {
            showAlert('清除失败: ' + (data.message || '未知错误'), 'danger');
        }
    })
    .catch(err => {
        showAlert('清除失败: ' + err, 'danger');
    })
    .finally(() => {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-trash"></i> 确认清除';
    });
}

$(document).ready(function() {
    var searchTimer = null;
    $('#searchInput').on('keyup', function() {
        clearTimeout(searchTimer);
        var keyword = $(this).val().toLowerCase();
        searchTimer = setTimeout(function() {
            $('#recordsTableBody tr').each(function() {
                var text = $(this).text().toLowerCase();
                $(this).toggle(text.indexOf(keyword) > -1);
            });
        }, 300);
    });
});
