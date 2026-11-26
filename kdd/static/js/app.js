const uploadZone = document.getElementById('uploadZone');
const fileInput = document.getElementById('fileInput');
const batchUploadZone = document.getElementById('batchUploadZone');
const batchFileInput = document.getElementById('batchFileInput');
const uploadProgress = document.getElementById('uploadProgress');
const resultSection = document.getElementById('resultSection');
const batchResult = document.getElementById('batchResult');

let currentRecordId = null;
let projectOptions = [];
let typeOptions = [];
let batchResults = [];
let batchCurrentIndex = 0;

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

batchUploadZone.addEventListener('click', () => batchFileInput.click());

batchUploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    batchUploadZone.classList.add('dragover');
});

batchUploadZone.addEventListener('dragleave', () => {
    batchUploadZone.classList.remove('dragover');
});

batchUploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    batchUploadZone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleBatchFiles(files);
    }
});

batchFileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleBatchFiles(e.target.files);
    }
});

function handleFile(file) {
    if (!file.type.startsWith('image/')) {
        alert('请上传图片文件');
        return;
    }
    if (file.size > 20 * 1024 * 1024) {
        alert('文件大小不能超过20MB');
        return;
    }

    uploadProgress.style.display = 'block';
    resultSection.style.display = 'none';
    currentRecordId = null;

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/upload', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        uploadProgress.style.display = 'none';
        if (data.success) {
            displayResult(data.data);
            loadRecords();
        } else {
            alert('识别失败: ' + data.message);
        }
    })
    .catch(err => {
        uploadProgress.style.display = 'none';
        alert('上传失败: ' + err);
    });
}

function handleBatchFiles(files) {
    const validFiles = Array.from(files).filter(f => {
        if (!f.type.startsWith('image/')) return false;
        if (f.size > 20 * 1024 * 1024) return false;
        return true;
    });

    if (validFiles.length === 0) {
        alert('请上传有效的图片文件');
        return;
    }

    uploadProgress.style.display = 'block';
    batchResult.style.display = 'none';

    const formData = new FormData();
    validFiles.forEach(file => {
        formData.append('files', file);
    });

    fetch('/api/upload/batch', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        uploadProgress.style.display = 'none';
        if (data.success) {
            document.getElementById('successCount').textContent = data.total;
            document.getElementById('failCount').textContent = data.failed;
            batchResult.style.display = 'block';
            loadRecords();
            if (data.data && data.data.length > 0) {
                batchResults = data.data;
                batchCurrentIndex = 0;
                displayBatchResult(0);
            }
        } else {
            alert('批量识别失败: ' + data.message);
        }
    })
    .catch(err => {
        uploadProgress.style.display = 'none';
        alert('批量上传失败: ' + err);
    });
}

function showBatchNavigation() {
    return `
        <div class="d-flex justify-content-between align-items-center mb-3 p-2 bg-light rounded">
            <button class="btn btn-sm btn-secondary" onclick="batchPrevRecord()" id="batchPrevBtn" ${batchCurrentIndex === 0 ? 'disabled' : ''}>
                <i class="fas fa-chevron-left me-1"></i>上一条
            </button>
            <span class="badge bg-primary" id="batchNavInfo">${batchCurrentIndex + 1} / ${batchResults.length}</span>
            <button class="btn btn-sm btn-secondary" onclick="batchNextRecord()" id="batchNextBtn" ${batchCurrentIndex >= batchResults.length - 1 ? 'disabled' : ''}>
                下一条<i class="fas fa-chevron-right ms-1"></i>
            </button>
        </div>
    `;
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

function displayBatchResult(index) {
    const r = batchResults[index];
    currentRecordId = r.id;
    document.getElementById('expressNo').value = r.express_no || '';
    document.getElementById('senderName').value = r.sender_name || '';
    document.getElementById('senderAddress').value = r.sender_address || '';
    document.getElementById('receiverName').value = r.receiver_name || '';
    document.getElementById('receiverAddress').value = r.receiver_address || '';
    document.getElementById('amount').value = r.amount || '';
    document.getElementById('expressDate').value = r.express_date || '';
    document.getElementById('fullText').value = r.full_text || '';
    document.getElementById('previewImage').src = '/uploads/' + r.image_path;
    document.getElementById('projectSelect').value = r.project || '';
    document.getElementById('typeSelect').value = r.record_type || '';

    document.getElementById('batchNavBar').classList.remove('d-none');
    document.getElementById('batchNavInfo').textContent = `${batchCurrentIndex + 1} / ${batchResults.length}`;
    document.getElementById('batchPrevBtn').disabled = batchCurrentIndex === 0;
    document.getElementById('batchNextBtn').disabled = batchCurrentIndex >= batchResults.length - 1;

    resultSection.style.display = 'block';
    resultSection.scrollIntoView({ behavior: 'smooth' });
}

function syncBatchFormToCurrent() {
    if (!batchResults[batchCurrentIndex]) return;
    var r = batchResults[batchCurrentIndex];
    r.sender_name = document.getElementById('senderName').value;
    r.sender_address = document.getElementById('senderAddress').value;
    r.receiver_name = document.getElementById('receiverName').value;
    r.receiver_address = document.getElementById('receiverAddress').value;
    r.amount = document.getElementById('amount').value;
    r.express_date = document.getElementById('expressDate').value;
    r.express_no = document.getElementById('expressNo').value;
    r.full_text = document.getElementById('fullText').value;
    r.project = document.getElementById('projectSelect').value;
    r.record_type = document.getElementById('typeSelect').value;
}

function showBatchResultsModal(results) {
    batchResults = results;
    const modalBody = document.getElementById('modalBody');
    let html = '<div class="list-group">';

    results.forEach((r, index) => {
        html += `
            <div class="list-group-item list-group-item-action" onclick="displayBatchResult(${index})" style="cursor:pointer;">
                <div class="d-flex w-100 justify-content-between align-items-center">
                    <h6 class="mb-1">${r.express_no || '单号'}</h6>
                    <small>${r.amount ? '¥' + r.amount : '-'}</small>
                </div>
                <p class="mb-1 small text-muted">
                    发件人: ${r.sender_name || '-'} → 收件人: ${r.receiver_name || '-'}
                </p>
                <small>${r.express_date || '-'}</small>
            </div>
        `;
    });

    html += '</div>';
    modalBody.innerHTML = html;

    document.querySelector('#detailModal .modal-title').textContent = `批量识别结果 (${results.length}条)`;
    new bootstrap.Modal(document.getElementById('detailModal')).show();
}

function loadOptions() {
    fetch('/api/options/project')
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                projectOptions = data.data || [];
                updateProjectSelect();
            }
        });

    fetch('/api/options/record_type')
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                typeOptions = data.data || [];
                updateTypeSelect();
            }
        });
}

function updateProjectSelect() {
    const select = document.getElementById('projectSelect');
    select.innerHTML = '<option value="">请选择</option>';
    projectOptions.forEach(opt => {
        select.innerHTML += `<option value="${opt}">${opt}</option>`;
    });
}

function updateTypeSelect() {
    const select = document.getElementById('typeSelect');
    select.innerHTML = '<option value="">请选择</option>';
    typeOptions.forEach(opt => {
        select.innerHTML += `<option value="${opt}">${opt}</option>`;
    });
}

function showAddOptionModal(type) {
    const optionType = type === 'type' ? 'record_type' : type;
    document.getElementById('optionType').value = optionType;
    document.getElementById('addOptionModalTitle').textContent = type === 'project' ? '添加新项目' : '添加新类型';
    document.getElementById('newOptionValue').value = '';

    const container = document.getElementById('existingOptions');
    const options = type === 'project' ? projectOptions : typeOptions;

    if (options.length > 0) {
        container.innerHTML = '<hr><small class="text-muted">现有选项（点击删除）：</small><div class="mt-2">' +
            options.map(opt => `<span class="badge bg-secondary me-2 mb-2" style="cursor:pointer;" onclick="deleteOption('${optionType}', '${opt}')">${opt} <i class="fas fa-times"></i></span>`).join('') +
            '</div>';
    } else {
        container.innerHTML = '';
    }

    new bootstrap.Modal(document.getElementById('addOptionModal')).show();
}

function addNewOption() {
    const type = document.getElementById('optionType').value;
    const value = document.getElementById('newOptionValue').value.trim();

    if (!value) {
        alert('请输入选项名称');
        return;
    }

    fetch(`/api/options/${type}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            if (type === 'project') {
                projectOptions.push(value);
                updateProjectSelect();
            } else if (type === 'record_type') {
                typeOptions.push(value);
                updateTypeSelect();
            }
            bootstrap.Modal.getInstance(document.getElementById('addOptionModal')).hide();
        } else {
            alert('添加失败');
        }
    });
}

function deleteOption(type, value) {
    if (!confirm(`确定要删除选项"${value}"吗？`)) return;

    fetch(`/api/options/${type}/${encodeURIComponent(value)}`, { method: 'DELETE' })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                if (type === 'project') {
                    projectOptions = projectOptions.filter(o => o !== value);
                    updateProjectSelect();
                } else if (type === 'record_type') {
                    typeOptions = typeOptions.filter(o => o !== value);
                    updateTypeSelect();
                }
                const displayType = type === 'record_type' ? 'type' : type;
                showAddOptionModal(displayType);
            }
        });
}

function displayResult(data) {
    currentRecordId = data.id;
    document.getElementById('expressNo').value = data.express_no || '';
    document.getElementById('senderName').value = data.sender_name || '';
    document.getElementById('senderAddress').value = data.sender_address || '';
    document.getElementById('receiverName').value = data.receiver_name || '';
    document.getElementById('receiverAddress').value = data.receiver_address || '';
    document.getElementById('amount').value = data.amount || '';
    document.getElementById('expressDate').value = data.express_date || '';
    document.getElementById('fullText').value = data.full_text || '';
    document.getElementById('previewImage').src = '/uploads/' + data.image_path;

    const projectSelect = document.getElementById('projectSelect');
    projectSelect.value = data.project || '';

    const typeSelect = document.getElementById('typeSelect');
    typeSelect.value = data.record_type || '';

    if (batchResults.length > 0) {
        document.getElementById('batchNavBar').classList.remove('d-none');
        document.getElementById('batchNavInfo').textContent = `${batchCurrentIndex + 1} / ${batchResults.length}`;
        document.getElementById('batchPrevBtn').disabled = batchCurrentIndex === 0;
        document.getElementById('batchNextBtn').disabled = batchCurrentIndex >= batchResults.length - 1;
    } else {
        document.getElementById('batchNavBar').classList.add('d-none');
    }

    resultSection.style.display = 'block';
}

function saveResult() {
    if (!currentRecordId) {
        alert('没有可保存的记录');
        return;
    }

    syncBatchFormToCurrent();

    const data = {
        sender_name: document.getElementById('senderName').value,
        sender_address: document.getElementById('senderAddress').value,
        receiver_name: document.getElementById('receiverName').value,
        receiver_address: document.getElementById('receiverAddress').value,
        amount: document.getElementById('amount').value,
        express_date: document.getElementById('expressDate').value,
        express_no: document.getElementById('expressNo').value,
        full_text: document.getElementById('fullText').value,
        project: document.getElementById('projectSelect').value,
        record_type: document.getElementById('typeSelect').value
    };

    fetch(`/api/records/${currentRecordId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(res => {
        if (!res.ok) {
            return res.text().then(text => {
                try {
                    var d = JSON.parse(text);
                    throw new Error(d.message || '服务器错误(' + res.status + ')');
                } catch (e) {
                    if (e.message && !e.message.startsWith('Unexpected')) throw e;
                    throw new Error('服务器错误(' + res.status + ')，请刷新页面后重试');
                }
            });
        }
        return res.json();
    })
    .then(result => {
        if (result.success) {
            alert('保存成功');
            loadRecords();
        } else {
            alert('保存失败: ' + result.message);
        }
    })
    .catch(err => {
        console.error('Save error:', err);
        alert('保存失败: ' + (err.message || '网络错误'));
    });
}

function cancelEdit() {
    currentRecordId = null;
    batchResults = [];
    batchCurrentIndex = 0;
    document.getElementById('expressNo').value = '';
    document.getElementById('senderName').value = '';
    document.getElementById('senderAddress').value = '';
    document.getElementById('receiverName').value = '';
    document.getElementById('receiverAddress').value = '';
    document.getElementById('amount').value = '';
    document.getElementById('expressDate').value = '';
    document.getElementById('fullText').value = '';
    document.getElementById('previewImage').src = '';
    document.getElementById('projectSelect').value = '';
    document.getElementById('typeSelect').value = '';
    document.getElementById('batchNavBar').classList.add('d-none');
    resultSection.style.display = 'none';
}

function loadRecords() {
    fetch('/api/records')
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                allRecords = data.data || [];
                renderTable(data.data);
            }
        });
}

function renderTable(records) {
    const tbody = document.getElementById('recordsTable');
    if (records.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted">暂无记录</td></tr>';
        return;
    }
    tbody.innerHTML = records.map(r => `
        <tr>
            <td class="text-nowrap">${r.id}</td>
            <td class="text-nowrap">${r.express_no || '-'}</td>
            <td class="text-nowrap d-none d-md-table-cell">${r.receiver_name || '-'}</td>
            <td class="text-wrap" style="max-width: 200px;">${r.receiver_address || '-'}</td>
            <td class="text-nowrap">${r.amount ? '¥' + r.amount : '-'}</td>
            <td class="text-nowrap d-none d-lg-table-cell">${r.express_date || '-'}</td>
            <td class="text-nowrap d-none d-lg-table-cell">${r.project || '-'}</td>
            <td class="text-nowrap d-none d-lg-table-cell">${r.record_type || '-'}</td>
            <td class="text-nowrap d-none d-lg-table-cell">${r.created_by || '-'}</td>
            <td class="text-nowrap">
                <button class="btn btn-sm btn-primary me-1" onclick="viewDetail(${r.id})"><i class="fas fa-eye"></i></button>
                <button class="btn btn-sm btn-success me-1" onclick="editRecord(${r.id})"><i class="fas fa-edit"></i></button>
                <button class="btn btn-sm btn-danger" onclick="deleteRecord(${r.id})"><i class="fas fa-trash"></i></button>
            </td>
        </tr>
    `).join('');
}

function viewDetail(id) {
    fetch(`/api/records/${id}`)
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                const r = data.data;
                document.getElementById('modalBody').innerHTML = `
                    <div class="row">
                        <div class="col-md-6">
                            <h6 class="text-warning"><i class="fas fa-barcode me-2"></i>单号</h6>
                            <p>${r.express_no || '-'}</p>
                        </div>
                        <div class="col-md-6">
                            <h6><i class="fas fa-folder me-2"></i>项目</h6>
                            <p>${r.project || '-'}</p>
                        </div>
                    </div>
                    <div class="row mt-2">
                        <div class="col-md-6">
                            <h6 class="text-primary"><i class="fas fa-user me-2"></i>发件人信息</h6>
                            <p><strong>姓名：</strong>${r.sender_name || '-'}</p>
                            <p><strong>地址：</strong>${r.sender_address || '-'}</p>
                        </div>
                        <div class="col-md-6">
                            <h6 class="text-danger"><i class="fas fa-user me-2"></i>收件人信息</h6>
                            <p><strong>姓名：</strong>${r.receiver_name || '-'}</p>
                            <p><strong>地址：</strong>${r.receiver_address || '-'}</p>
                        </div>
                    </div>
                    <div class="row mt-3">
                        <div class="col-md-4">
                            <p><strong>金额：</strong>${r.amount ? '¥' + r.amount : '-'}</p>
                        </div>
                        <div class="col-md-4">
                            <p><strong>日期：</strong>${r.express_date || '-'}</p>
                        </div>
                        <div class="col-md-4">
                            <p><strong>类型：</strong>${r.record_type || '-'}</p>
                        </div>
                    </div>
                    <div class="row mt-2">
                        <div class="col-md-12">
                            <p><strong>创建时间：</strong>${r.created_at || '-'}</p>
                        </div>
                    </div>
                    ${r.image_path ? `<div class="text-center mt-3"><img src="/uploads/${r.image_path}" class="preview-img" alt="快递单图片"></div>` : ''}
                    ${r.full_text ? `<div class="mt-3"><strong>完整识别文本：</strong><pre class="bg-light p-3 rounded" style="white-space:pre-wrap;">${r.full_text}</pre></div>` : ''}
                `;
                new bootstrap.Modal(document.getElementById('detailModal')).show();
            }
        });
}

function editRecord(id) {
    fetch(`/api/records/${id}`)
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                displayResult(data.data);
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        });
}

function deleteRecord(id) {
    if (!confirm('确定要删除这条记录吗？')) return;
    fetch(`/api/records/${id}`, { method: 'DELETE' })
        .then(res => {
            if (!res.ok) {
                return res.text().then(text => {
                    try {
                        var d = JSON.parse(text);
                        throw new Error(d.message || '服务器错误(' + res.status + ')');
                    } catch (e) {
                        if (e.message && !e.message.startsWith('Unexpected')) throw e;
                        throw new Error('服务器错误(' + res.status + ')，请刷新页面后重试');
                    }
                });
            }
            return res.json();
        })
        .then(data => {
            if (data.success) {
                if (currentRecordId === id) {
                    cancelEdit();
                }
                loadRecords();
                alert('删除成功');
            } else {
                alert('删除失败: ' + data.message);
            }
        })
        .catch(err => {
            alert('删除失败: ' + (err.message || '网络错误'));
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
        alert('开始日期格式不正确，应为 YYYY-MM-DD');
        return null;
    }
    if (end && !isValidExportDate(end)) {
        alert('结束日期格式不正确，应为 YYYY-MM-DD');
        return null;
    }
    if (start && end && start > end) {
        alert('开始日期不能晚于结束日期');
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
    window.location.href = '/api/export/' + type + (query ? '?' + query : '');
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
    checkLoginStatus();
    loadOptions();
    loadRecords();
});

function checkLoginStatus() {
    fetch('/api/check_login')
        .then(res => res.json())
        .then(data => {
            if (data.logged_in) {
                document.getElementById('userInfo').textContent = `欢迎，${data.username}`;
                if (data.role === 'admin') {
                    document.getElementById('userManageBtn').style.display = 'inline-block';
                    document.getElementById('metricsBtn').style.display = 'inline-block';
                }
            } else {
                window.location.href = '/login.html';
            }
        });
}

function logout() {
    fetch('/api/logout', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            window.location.href = '/';
        });
}