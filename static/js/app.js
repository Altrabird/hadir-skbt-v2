/**
 * Hadir@SKBT v2 — Frontend Application
 */

let currentStudents = [];
let currentClass = "";
let selectedDate = "";

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    const picker = document.getElementById("date-picker");
    const today = new Date();
    const y = today.getFullYear();
    const m = String(today.getMonth() + 1).padStart(2, "0");
    const d = String(today.getDate()).padStart(2, "0");
    selectedDate = `${y}-${m}-${d}`;
    picker.value = selectedDate;

    picker.addEventListener("change", () => {
        selectedDate = picker.value;
        refreshDashboard();
    });

    loadClasses();
    refreshDashboard();
});

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function showLoading() { document.getElementById("loading-overlay").classList.remove("hidden"); }
function hideLoading() { document.getElementById("loading-overlay").classList.add("hidden"); }

function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    const styles = {
        success: "bg-emerald-600",
        error: "bg-red-600",
        info: "bg-blue-600",
    };
    const icons = {
        success: "fa-circle-check",
        error: "fa-circle-exclamation",
        info: "fa-circle-info",
    };

    const el = document.createElement("div");
    el.className = `flex items-center gap-2 px-3 py-2.5 rounded-lg shadow-xl text-xs font-semibold text-white ${styles[type] || styles.info} animate-slide-in`;
    el.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i><span>${message}</span>`;
    container.appendChild(el);

    setTimeout(() => {
        el.style.opacity = "0";
        el.style.transform = "translateX(100%)";
        el.style.transition = "all 0.25s ease";
        setTimeout(() => el.remove(), 250);
    }, 3000);
}

function toggleSection(id) {
    const body = document.getElementById(id);
    const btn = body.previousElementSibling;
    const chevron = btn.querySelector(".section-chevron");
    body.classList.toggle("hidden");
    chevron.classList.toggle("rotate-180");
}

async function apiFetch(url, options = {}) {
    try {
        const res = await fetch(url, options);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: res.statusText }));
            throw new Error(err.error || res.statusText);
        }
        return res;
    } catch (e) {
        showToast(e.message, "error");
        throw e;
    }
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

// ---------------------------------------------------------------------------
// Load classes
// ---------------------------------------------------------------------------
async function loadClasses() {
    try {
        const res = await apiFetch("/api/classes");
        const classes = await res.json();
        const select = document.getElementById("class-select");
        select.innerHTML = '<option value="">— Pilih kelas —</option>';
        classes.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            select.appendChild(opt);
        });
    } catch { /* toast shown */ }
}

// ---------------------------------------------------------------------------
// Load students
// ---------------------------------------------------------------------------
async function loadStudents() {
    const select = document.getElementById("class-select");
    currentClass = select.value;
    const list = document.getElementById("student-list");
    const submitBtn = document.getElementById("submit-btn");
    const notice = document.getElementById("existing-notice");
    notice.classList.add("hidden");
    notice.classList.remove("flex");

    if (!currentClass) {
        list.innerHTML = '<p class="text-sm text-gray-400 py-6 text-center"><i class="fa-solid fa-users text-gray-300 mr-1"></i> Sila pilih kelas.</p>';
        submitBtn.disabled = true;
        return;
    }

    showLoading();
    try {
        const [studentsRes, attendanceRes] = await Promise.all([
            apiFetch(`/api/students/${encodeURIComponent(currentClass)}`),
            apiFetch(`/api/attendance/${selectedDate}/${encodeURIComponent(currentClass)}`),
        ]);
        currentStudents = await studentsRes.json();
        const existing = await attendanceRes.json();

        const prefill = {};
        if (existing.length > 0) {
            notice.classList.remove("hidden");
            notice.classList.add("flex");
            existing.forEach(r => { prefill[r.name] = r.status; });
        }

        list.innerHTML = "";
        currentStudents.forEach((s, i) => {
            const isAbsent = prefill[s.name] === "Absent";
            const row = document.createElement("div");
            row.className = "student-row";
            row.innerHTML = `
                <div class="flex items-center gap-2 min-w-0">
                    <span class="w-5 text-[10px] text-gray-400 font-mono text-right">${i + 1}</span>
                    ${s.is_rmt ? '<i class="fa-solid fa-bowl-rice text-[10px] text-orange-400" title="RMT"></i>' : ""}
                    <span class="text-xs font-semibold text-gray-700 truncate">${escapeHtml(s.name)}</span>
                </div>
                <button type="button" onclick="toggleStudent(this)" data-index="${i}"
                    class="shrink-0 status-toggle ${isAbsent ? "absent" : "present"}">
                    <span class="toggle-label">${isAbsent ? "Tidak Hadir" : "Hadir"}</span>
                </button>
            `;
            list.appendChild(row);
        });
        submitBtn.disabled = false;
    } catch {
        list.innerHTML = '<p class="text-xs text-red-500 py-6 text-center"><i class="fa-solid fa-triangle-exclamation mr-1"></i>Gagal memuatkan senarai murid.</p>';
        submitBtn.disabled = true;
    } finally {
        hideLoading();
    }
}

// ---------------------------------------------------------------------------
// Toggle
// ---------------------------------------------------------------------------
function toggleStudent(btn) {
    const isPresent = btn.classList.contains("present");
    btn.classList.toggle("present", !isPresent);
    btn.classList.toggle("absent", isPresent);
    btn.querySelector(".toggle-label").textContent = isPresent ? "Tidak Hadir" : "Hadir";
}

function setAllStatus(present) {
    document.querySelectorAll(".status-toggle").forEach(btn => {
        btn.classList.toggle("present", present);
        btn.classList.toggle("absent", !present);
        btn.querySelector(".toggle-label").textContent = present ? "Hadir" : "Tidak Hadir";
    });
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------
async function submitAttendance() {
    if (!currentClass || currentStudents.length === 0) return;

    const btns = document.querySelectorAll(".status-toggle");
    const students = [];
    btns.forEach((btn, i) => {
        students.push({
            name: currentStudents[i].name,
            status: btn.classList.contains("absent") ? "Absent" : "Present",
        });
    });

    const absentCount = students.filter(s => s.status === "Absent").length;
    if (!confirm(`Hantar kehadiran untuk ${currentClass}?\n\n${students.length} murid, ${absentCount} tidak hadir.`)) return;

    showLoading();
    try {
        const res = await apiFetch("/api/attendance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ date: selectedDate, class: currentClass, students }),
        });
        const data = await res.json();
        showToast(`Berjaya! ${data.absent_count} murid ditandakan tidak hadir.`, "success");
        refreshDashboard();
    } catch { /* toast shown */ }
    finally { hideLoading(); }
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
async function refreshDashboard() {
    const emptyEl = document.getElementById("dashboard-empty");
    const contentEl = document.getElementById("dashboard-content");

    try {
        const res = await apiFetch(`/api/dashboard/${selectedDate}`);
        const d = await res.json();

        if (!d.has_data) {
            emptyEl.classList.remove("hidden");
            contentEl.classList.add("hidden");
            return;
        }

        emptyEl.classList.add("hidden");
        contentEl.classList.remove("hidden");

        setText("m-present", d.total_present);
        setText("m-present-sub", `daripada ${d.total_marked} murid`);
        setText("m-absent", d.total_absent);
        setText("m-absent-sub", `${d.total_marked > 0 ? (d.total_absent / d.total_marked * 100).toFixed(1) : 0}%`);
        setText("m-classes", d.classes_updated);
        setText("m-classes-sub", `daripada ${d.total_classes} kelas`);
        setText("m-rate", `${d.attendance_rate}%`);

        setText("pagi-present", d.pagi.present);
        setText("pagi-absent", d.pagi.absent);
        setText("pagi-pct", `${d.pagi.present_pct}%`);
        setText("petang-present", d.petang.present);
        setText("petang-absent", d.petang.absent);
        setText("petang-pct", `${d.petang.present_pct}%`);

        // Year summary
        renderYearSummary(d.by_year || []);

        const rmt = d.rmt;
        if (rmt.total_marked === 0) {
            document.getElementById("rmt-no-data").classList.remove("hidden");
            document.getElementById("rmt-metrics").classList.add("hidden");
        } else {
            document.getElementById("rmt-no-data").classList.add("hidden");
            document.getElementById("rmt-metrics").classList.remove("hidden");
            setText("rmt-pagi", rmt.pagi_present);
            setText("rmt-pagi-sub", `drp ${rmt.pagi_total}`);
            setText("rmt-petang", rmt.petang_present);
            setText("rmt-petang-sub", `drp ${rmt.petang_total}`);
            setText("rmt-total", rmt.total_present);
            setText("rmt-coverage", `${rmt.coverage_pct}%`);
        }

        const dlBtn = document.getElementById("download-all-btn");
        const hasRecorded = d.class_summary && d.class_summary.some(r => r.status === "updated");
        if (hasRecorded) {
            dlBtn.href = `/api/export/${selectedDate}`;
            dlBtn.classList.remove("hidden");
            dlBtn.classList.add("inline-flex");
        } else {
            dlBtn.classList.add("hidden");
            dlBtn.classList.remove("inline-flex");
        }

        renderClassTable(d.class_summary);
    } catch { /* toast shown */ }
}

// Cache last year summary for modal access
let _lastYearSummary = [];

function renderYearSummary(byYear) {
    _lastYearSummary = byYear || [];
    const container = document.getElementById("year-summary-content");
    if (!byYear || byYear.length === 0) {
        container.innerHTML = '<p class="text-xs text-gray-300 text-center py-2">Tiada data.</p>';
        return;
    }

    // Split into Petang (1-3) and Pagi (4-6)
    const petang = byYear.filter(y => y.session === "Petang");
    const pagi   = byYear.filter(y => y.session === "Pagi");

    function yearCard(y) {
        const pct = y.pct;
        const color = pct >= 95 ? "emerald" : pct >= 90 ? "blue" : pct >= 80 ? "amber" : "red";
        const barW  = Math.max(pct, 2);
        const notMarked = y.not_marked || 0;
        const warnIcon = notMarked > 0
            ? `<button onclick="showUnmarkedDetails('${y.year}')" class="text-[10px] text-amber-500 hover:text-amber-600 ml-0.5 cursor-pointer" title="Klik untuk lihat ${notMarked} murid belum direkod"><i class="fa-solid fa-circle-exclamation"></i></button>`
            : '';
        return `
            <div class="flex items-center gap-2 py-1">
                <span class="text-[10px] font-black text-gray-700 w-12 shrink-0">${escapeHtml(y.label)}</span>
                <div class="flex-1 h-4 bg-gray-100 rounded-full overflow-hidden">
                    <div class="h-full rounded-full bg-${color}-500 transition-all duration-500"
                         style="width:${barW}%"></div>
                </div>
                <span class="text-xs font-black text-${color}-600 w-11 text-right shrink-0">${pct}%</span>
                <span class="text-[9px] text-gray-400 w-16 text-right shrink-0 inline-flex items-center justify-end gap-0.5">${y.present}/${y.total}${warnIcon}</span>
            </div>`;
    }

    let html = '<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4">';

    // Pagi group
    if (pagi.length) {
        html += `<div>
            <div class="flex items-center gap-1 mb-1">
                <i class="fa-solid fa-sun text-[9px] text-amber-500"></i>
                <span class="text-[9px] font-bold text-amber-600 uppercase tracking-wider">Sesi Pagi</span>
            </div>
            ${pagi.map(yearCard).join("")}
        </div>`;
    }

    // Petang group
    if (petang.length) {
        html += `<div>
            <div class="flex items-center gap-1 mb-1">
                <i class="fa-solid fa-moon text-[9px] text-indigo-500"></i>
                <span class="text-[9px] font-bold text-indigo-600 uppercase tracking-wider">Sesi Petang</span>
            </div>
            ${petang.map(yearCard).join("")}
        </div>`;
    }

    html += "</div>";
    container.innerHTML = html;
}

function showUnmarkedDetails(yearKey) {
    const y = _lastYearSummary.find(x => x.year === yearKey);
    if (!y || !y.not_marked_classes || y.not_marked_classes.length === 0) return;

    let listHtml = "";
    y.not_marked_classes.forEach(c => {
        const studentRows = c.students.map((n, i) =>
            `<div class="flex items-center gap-1.5 py-0.5 text-[10px]">
                <span class="text-gray-400 w-4 text-right">${i + 1}.</span>
                <span class="text-gray-700">${escapeHtml(n)}</span>
            </div>`
        ).join("");
        listHtml += `
            <div class="border border-gray-200 rounded-lg p-2.5 mb-2">
                <div class="flex items-center justify-between mb-1.5 pb-1.5 border-b border-gray-100">
                    <span class="font-bold text-xs text-gray-800">${escapeHtml(c.class)}</span>
                    <span class="text-[9px] font-bold text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">${c.count} murid</span>
                </div>
                ${studentRows}
            </div>`;
    });

    const modal = document.getElementById("unmarked-modal");
    document.getElementById("unmarked-modal-title").textContent = `${y.label}: ${y.not_marked} Murid Belum Direkod`;
    document.getElementById("unmarked-modal-content").innerHTML = listHtml;
    modal.classList.remove("hidden");
}

function closeUnmarkedModal() {
    document.getElementById("unmarked-modal").classList.add("hidden");
}

function renderClassTable(summary) {
    const tbody = document.getElementById("class-table-body");
    tbody.innerHTML = "";

    summary.forEach(row => {
        const tr = document.createElement("tr");

        const statusBadge = row.status === "updated"
            ? '<span class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-700"><i class="fa-solid fa-circle-check text-[8px]"></i> OK</span>'
            : '<span class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-gray-100 text-gray-400"><i class="fa-regular fa-clock text-[8px]"></i> Belum</span>';

        const sessionBadge = row.session === "Pagi"
            ? '<span class="text-[10px] font-bold text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded"><i class="fa-solid fa-sun text-[8px] mr-0.5"></i>Pagi</span>'
            : row.session === "Petang"
                ? '<span class="text-[10px] font-bold text-indigo-700 bg-indigo-100 px-1.5 py-0.5 rounded"><i class="fa-solid fa-moon text-[8px] mr-0.5"></i>Petang</span>'
                : '<span class="text-[10px] text-gray-400">—</span>';

        const absentNames = row.absent_names.length > 0
            ? row.absent_names.map(n => `<span class="inline-block bg-red-50 text-red-600 text-[10px] font-medium px-1 py-0.5 rounded mr-0.5 mb-0.5">${escapeHtml(n)}</span>`).join("")
            : '<span class="text-gray-300 text-[10px]">—</span>';

        const csvBtn = row.status === "updated"
            ? `<a href="/api/export/${selectedDate}/${encodeURIComponent(row.class)}" class="inline-flex items-center justify-center w-6 h-6 rounded bg-blue-50 hover:bg-blue-100 transition" title="CSV"><i class="fa-solid fa-download text-[9px] text-blue-500"></i></a>`
            : '<span class="text-gray-200">—</span>';

        const mobileDlBtn = row.status === "updated"
            ? `<a href="/api/export/${selectedDate}/${encodeURIComponent(row.class)}" class="inline-flex items-center justify-center gap-1 w-full px-2 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-[10px] font-bold transition shadow-sm"><i class="fa-solid fa-download text-[9px]"></i> Muat Turun CSV</a>`
            : '';

        tr.innerHTML = `
            <td data-label="Kelas" class="px-3 py-2 font-bold text-gray-800">${escapeHtml(row.class)}</td>
            <td data-label="Sesi" class="px-3 py-2">${sessionBadge}</td>
            <td data-label="Status" class="px-3 py-2 text-center">${statusBadge}</td>
            <td data-label="Masa" class="px-3 py-2 text-center text-[10px] text-gray-500">${row.time}</td>
            <td data-label="Hadir" class="px-3 py-2 text-center font-bold text-emerald-600">${row.present}</td>
            <td data-label="Kadar" class="px-3 py-2 text-center text-[10px] text-gray-500">${row.present_pct}%</td>
            <td data-label="T/Hadir" class="px-3 py-2 text-center font-bold ${row.absent > 0 ? "text-red-600" : "text-gray-300"}">${row.absent}</td>
            <td data-label="Tidak Hadir" class="px-3 py-2">${absentNames}</td>
            <td data-label="" class="px-3 py-2 text-center td-csv">${csvBtn}</td>
            <td data-label="" class="px-3 py-2 td-mobile-dl">${mobileDlBtn}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
let sidebarOpen = false;
let sidebarStudentsList = null; // cached from API

function applySidebarFull(full) {
    const panel = document.getElementById("sidebar-panel");
    const btn = document.getElementById("sidebar-full-btn");
    panel.classList.toggle("sidebar-full", full);
    if (btn) btn.title = full ? "Paparan kecil" : "Paparan penuh";
    try { localStorage.setItem("sidebarFull", full ? "1" : "0"); } catch { /* ignore */ }
}

function toggleSidebarFull() {
    applySidebarFull(!document.getElementById("sidebar-panel").classList.contains("sidebar-full"));
}

function restoreSidebarFull() {
    let saved = null;
    try { saved = localStorage.getItem("sidebarFull"); } catch { /* ignore */ }
    if (saved === "1") applySidebarFull(true);
}

function toggleSidebar() {
    const panel = document.getElementById("sidebar-panel");
    const overlay = document.getElementById("sidebar-overlay");
    const toggleBtn = document.getElementById("sidebar-toggle-btn");

    sidebarOpen = !sidebarOpen;
    panel.classList.toggle("open", sidebarOpen);
    overlay.classList.toggle("hidden", !sidebarOpen);
    toggleBtn.classList.toggle("hidden", sidebarOpen);

    if (sidebarOpen) restoreSidebarFull();
    if (sidebarOpen && !sidebarStudentsList) {
        loadSidebarData();
    }
}

async function loadSidebarData() {
    try {
        const res = await apiFetch("/api/summary/students-list");
        sidebarStudentsList = await res.json();

        // Populate class dropdowns in all tabs
        const classFilter = document.getElementById("sidebar-class-filter");
        const classSelect = document.getElementById("sidebar-class-select");

        let classOpts = '<option value="">— Pilih kelas —</option>';
        sidebarStudentsList.forEach(c => {
            classOpts += `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`;
        });
        classFilter.innerHTML = classOpts;
        classSelect.innerHTML = classOpts;
        // The Rekod tab's selects are populated by applySebabAuth(), scoped to
        // the classes the logged-in teacher is assigned to.
    } catch { /* toast shown */ }
}

function switchSidebarTab(tab) {
    const tabs = ["student", "class", "rmt", "sebab"];
    tabs.forEach(t => {
        const btn = document.getElementById(`tab-${t}`);
        const content = document.getElementById(`tab-content-${t}`);
        if (t === tab) {
            btn.classList.add("active");
            content.classList.remove("hidden");
        } else {
            btn.classList.remove("active");
            content.classList.add("hidden");
        }
    });

    // Init RMT month picker on first open
    if (tab === "rmt") {
        const picker = document.getElementById("rmt-month-picker");
        if (!picker.value) {
            const now = new Date();
            picker.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
            loadRmtSummary();
        }
    }

    // Init reason-recording prerequisites on first open
    if (tab === "sebab") {
        initSebabTab();
    }
}

// ---------------------------------------------------------------------------
// Sidebar: Student Tab
// ---------------------------------------------------------------------------
function loadSidebarStudents() {
    const classFilter = document.getElementById("sidebar-class-filter");
    const studentSelect = document.getElementById("sidebar-student-select");
    const cls = classFilter.value;

    studentSelect.innerHTML = '<option value="">— Pilih murid —</option>';
    document.getElementById("student-summary-content").innerHTML = `
        <div class="text-center py-8">
            <i class="fa-solid fa-user-graduate text-3xl text-gray-200"></i>
            <p class="text-xs text-gray-400 mt-2">Pilih murid untuk melihat rumusan kehadiran.</p>
        </div>`;

    if (!cls || !sidebarStudentsList) return;

    const classData = sidebarStudentsList.find(c => c.name === cls);
    if (!classData) return;

    classData.students.forEach(name => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        studentSelect.appendChild(opt);
    });
}

async function loadStudentSummary() {
    const studentName = document.getElementById("sidebar-student-select").value;
    const container = document.getElementById("student-summary-content");

    if (!studentName) {
        container.innerHTML = `
            <div class="text-center py-8">
                <i class="fa-solid fa-user-graduate text-3xl text-gray-200"></i>
                <p class="text-xs text-gray-400 mt-2">Pilih murid untuk melihat rumusan kehadiran.</p>
            </div>`;
        return;
    }

    container.innerHTML = `<div class="text-center py-6"><div class="w-6 h-6 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin mx-auto"></div></div>`;

    try {
        const res = await apiFetch(`/api/summary/student/${encodeURIComponent(studentName)}`);
        const d = await res.json();

        const rateColor = d.rate >= 90 ? "emerald" : d.rate >= 75 ? "amber" : "red";
        const rateIcon = d.rate >= 90 ? "fa-face-smile" : d.rate >= 75 ? "fa-face-meh" : "fa-face-frown";

        container.innerHTML = `
            <!-- Name & Class -->
            <div class="bg-gradient-to-r from-indigo-500 to-blue-500 rounded-xl p-3 text-white">
                <div class="flex items-center gap-2 mb-1">
                    <i class="fa-solid fa-user-graduate text-sm text-indigo-200"></i>
                    <span class="text-xs font-bold truncate">${escapeHtml(d.name)}</span>
                </div>
                <span class="text-[10px] text-indigo-200"><i class="fa-solid fa-chalkboard mr-0.5"></i>${escapeHtml(d.class)}</span>
            </div>

            <!-- Rate Card -->
            <div class="bg-${rateColor}-50 border border-${rateColor}-200 rounded-xl p-3 text-center">
                <i class="fa-solid ${rateIcon} text-2xl text-${rateColor}-500 mb-1"></i>
                <p class="text-2xl font-black text-${rateColor}-600">${d.rate}%</p>
                <p class="text-[10px] font-bold text-${rateColor}-500 uppercase tracking-wider">Kadar Kehadiran</p>
                <!-- Bar -->
                <div class="attendance-bar mt-2">
                    <div class="attendance-bar-fill" style="width: ${d.rate}%"></div>
                </div>
            </div>

            <!-- Stats Grid -->
            <div class="grid grid-cols-3 gap-1.5">
                <div class="bg-blue-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-blue-600">${d.total_days}</p>
                    <p class="text-[9px] font-bold text-blue-500">Hari Rekod</p>
                </div>
                <div class="bg-emerald-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-emerald-600">${d.present}</p>
                    <p class="text-[9px] font-bold text-emerald-500">Hadir</p>
                </div>
                <div class="bg-red-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-red-600">${d.absent}</p>
                    <p class="text-[9px] font-bold text-red-500">Tidak Hadir</p>
                </div>
            </div>

            <!-- Absent Dates -->
            ${d.absent_dates.length > 0 ? `
                <div>
                    <div class="flex items-center gap-1 mb-1.5">
                        <i class="fa-solid fa-calendar-xmark text-[10px] text-red-400"></i>
                        <span class="text-[10px] font-bold text-gray-600 uppercase tracking-wider">Tarikh Tidak Hadir</span>
                    </div>
                    <div class="space-y-0.5">
                        ${d.absent_dates.map(date => `
                            <div class="flex items-center gap-1.5 px-2 py-1 bg-red-50 rounded text-[10px] font-medium text-red-600">
                                <i class="fa-solid fa-circle text-[4px]"></i>
                                ${formatDate(date)}
                            </div>
                        `).join("")}
                    </div>
                </div>
            ` : `
                <div class="text-center py-3">
                    <i class="fa-solid fa-award text-xl text-emerald-400"></i>
                    <p class="text-[10px] text-emerald-600 font-bold mt-1">Kehadiran penuh! Tiada rekod tidak hadir.</p>
                </div>
            `}
        `;
    } catch {
        container.innerHTML = `<p class="text-xs text-red-500 text-center py-4">Gagal memuatkan data.</p>`;
    }
}

// ---------------------------------------------------------------------------
// Sidebar: Class Tab
// ---------------------------------------------------------------------------
async function loadClassSummary() {
    const className = document.getElementById("sidebar-class-select").value;
    const container = document.getElementById("class-summary-content");

    if (!className) {
        container.innerHTML = `
            <div class="text-center py-8">
                <i class="fa-solid fa-school text-3xl text-gray-200"></i>
                <p class="text-xs text-gray-400 mt-2">Pilih kelas untuk melihat rumusan kehadiran.</p>
            </div>`;
        return;
    }

    container.innerHTML = `<div class="text-center py-6"><div class="w-6 h-6 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin mx-auto"></div></div>`;

    try {
        const res = await apiFetch(`/api/summary/class/${encodeURIComponent(className)}`);
        const d = await res.json();

        const rateColor = d.avg_rate >= 90 ? "emerald" : d.avg_rate >= 75 ? "amber" : "red";
        const sessionIcon = d.session === "Pagi" ? "fa-sun" : "fa-moon";
        const sessionColor = d.session === "Pagi" ? "amber" : "indigo";

        let studentRows = "";
        d.student_summary.forEach((s, i) => {
            const sColor = s.rate >= 90 ? "emerald" : s.rate >= 75 ? "amber" : "red";
            studentRows += `
                <div class="summary-student-row">
                    <div class="flex items-center gap-1.5 min-w-0 flex-1">
                        <span class="w-4 text-[9px] text-gray-400 text-right">${i + 1}</span>
                        <span class="font-semibold text-gray-700 truncate">${escapeHtml(s.name)}</span>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        <span class="text-emerald-600 font-bold w-5 text-right">${s.present}</span>
                        <span class="text-red-500 font-bold w-5 text-right">${s.absent}</span>
                        <span class="font-black text-${sColor}-600 w-10 text-right">${s.rate}%</span>
                    </div>
                </div>
            `;
        });

        container.innerHTML = `
            <!-- Class Header -->
            <div class="bg-gradient-to-r from-${sessionColor}-500 to-${sessionColor}-600 rounded-xl p-3 text-white">
                <div class="flex items-center gap-2 mb-0.5">
                    <i class="fa-solid fa-chalkboard-user text-sm text-${sessionColor}-200"></i>
                    <span class="text-xs font-bold">${escapeHtml(d.class)}</span>
                </div>
                <span class="text-[10px] text-${sessionColor}-200">
                    <i class="fa-solid ${sessionIcon} mr-0.5"></i>Sesi ${d.session}
                    &bull; ${d.total_students} murid
                </span>
            </div>

            <!-- Summary Stats -->
            <div class="grid grid-cols-3 gap-1.5">
                <div class="bg-blue-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-blue-600">${d.total_days}</p>
                    <p class="text-[9px] font-bold text-blue-500">Hari Rekod</p>
                </div>
                <div class="bg-${rateColor}-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-${rateColor}-600">${d.avg_rate}%</p>
                    <p class="text-[9px] font-bold text-${rateColor}-500">Purata Kadar</p>
                </div>
                <div class="bg-purple-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-purple-600">${d.total_students}</p>
                    <p class="text-[9px] font-bold text-purple-500">Jumlah Murid</p>
                </div>
            </div>

            <!-- Attendance Bar -->
            <div>
                <div class="flex justify-between text-[9px] font-bold text-gray-500 mb-0.5">
                    <span>Purata Kehadiran</span>
                    <span class="text-${rateColor}-600">${d.avg_rate}%</span>
                </div>
                <div class="attendance-bar">
                    <div class="attendance-bar-fill" style="width: ${d.avg_rate}%"></div>
                </div>
            </div>

            <!-- Per-Student Table -->
            <div>
                <div class="flex items-center justify-between mb-1.5">
                    <div class="flex items-center gap-1">
                        <i class="fa-solid fa-list-ol text-[10px] text-gray-400"></i>
                        <span class="text-[10px] font-bold text-gray-600 uppercase tracking-wider">Senarai Murid</span>
                    </div>
                    <div class="flex items-center gap-2 text-[8px] font-bold text-gray-400 uppercase">
                        <span class="text-emerald-500">H</span>
                        <span class="text-red-500">TH</span>
                        <span>%</span>
                    </div>
                </div>
                <div class="max-h-[300px] overflow-y-auto rounded-lg border border-gray-100">
                    ${studentRows}
                </div>
            </div>
        `;
    } catch {
        container.innerHTML = `<p class="text-xs text-red-500 text-center py-4">Gagal memuatkan data.</p>`;
    }
}

// ---------------------------------------------------------------------------
// Sidebar: RMT Tab
// ---------------------------------------------------------------------------
async function loadRmtSummary() {
    const month = document.getElementById("rmt-month-picker").value;
    const container = document.getElementById("rmt-summary-content");
    const dlSection = document.getElementById("rmt-download-section");

    if (!month) {
        container.innerHTML = `<div class="text-center py-8"><i class="fa-solid fa-bowl-rice text-3xl text-gray-200"></i><p class="text-xs text-gray-400 mt-2">Pilih bulan untuk melihat laporan RMT.</p></div>`;
        dlSection.classList.add("hidden");
        return;
    }

    container.innerHTML = `<div class="text-center py-6"><div class="w-6 h-6 border-2 border-orange-200 border-t-orange-600 rounded-full animate-spin mx-auto"></div></div>`;

    try {
        const res = await apiFetch(`/api/summary/rmt/${month}`);
        const d = await res.json();

        // Populate export class dropdown
        const exportSelect = document.getElementById("rmt-export-class");
        exportSelect.innerHTML = '<option value="">Semua Kelas</option>';
        d.classes.forEach(cls => {
            exportSelect.innerHTML += `<option value="${escapeHtml(cls.class)}">${escapeHtml(cls.class)}</option>`;
        });
        dlSection.classList.remove("hidden");

        const months = ["","Januari","Februari","Mac","April","Mei","Jun","Julai","Ogos","September","Oktober","November","Disember"];
        const monthLabel = `${months[d.month_num]} ${d.year}`;
        const rateColor = d.totals.avg_rate >= 90 ? "emerald" : d.totals.avg_rate >= 75 ? "amber" : "red";

        let html = `
            <!-- Header -->
            <div class="bg-gradient-to-r from-orange-500 to-amber-500 rounded-xl p-3 text-white">
                <div class="flex items-center gap-2 mb-0.5">
                    <i class="fa-solid fa-bowl-rice text-sm text-orange-200"></i>
                    <span class="text-xs font-bold">Laporan RMT Bulanan</span>
                </div>
                <span class="text-[10px] text-orange-100">${monthLabel}</span>
            </div>

            <!-- Stats -->
            <div class="grid grid-cols-2 gap-1.5">
                <div class="bg-orange-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-orange-600">${d.totals.total_rmt}</p>
                    <p class="text-[9px] font-bold text-orange-500">Jumlah Murid RMT</p>
                </div>
                <div class="bg-${rateColor}-50 rounded-lg p-2 text-center">
                    <p class="text-lg font-black text-${rateColor}-600">${d.totals.avg_rate}%</p>
                    <p class="text-[9px] font-bold text-${rateColor}-500">Purata Kehadiran</p>
                </div>
            </div>
        `;

        // Per-class sections
        d.classes.forEach(cls => {
            const sessionIcon = cls.session === "Pagi" ? "fa-sun" : "fa-moon";
            const sessionColor = cls.session === "Pagi" ? "amber" : "indigo";

            let studentRows = "";
            cls.students.forEach((s, i) => {
                const sColor = s.rate >= 90 ? "emerald" : s.rate >= 75 ? "amber" : "red";
                studentRows += `
                    <div class="summary-student-row">
                        <div class="flex items-center gap-1.5 min-w-0 flex-1">
                            <span class="w-4 text-[9px] text-gray-400 text-right">${i + 1}</span>
                            <span class="font-semibold text-gray-700 truncate">${escapeHtml(s.name)}</span>
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <span class="text-emerald-600 font-bold w-5 text-right">${s.present}</span>
                            <span class="text-red-500 font-bold w-5 text-right">${s.absent}</span>
                            <span class="font-black text-${sColor}-600 w-10 text-right">${s.rate}%</span>
                        </div>
                    </div>
                `;
            });

            // Daily grid
            let dayHeaders = "";
            d.school_days.forEach(day => {
                dayHeaders += `<th class="rmt-cell text-gray-500">${parseInt(day.split("-")[2])}</th>`;
            });

            let dailyRows = "";
            cls.students.forEach(s => {
                let cells = "";
                s.daily.forEach(day => {
                    const cellClass = day.status === "H" ? "hadir" : day.status === "TH" ? "tidak-hadir" : "no-data";
                    cells += `<td class="rmt-cell ${cellClass}">${day.status}</td>`;
                });
                dailyRows += `<tr><td class="px-1 py-0.5 text-[9px] font-semibold text-gray-700 whitespace-nowrap sticky left-0 bg-white">${escapeHtml(s.name)}</td>${cells}</tr>`;
            });

            html += `
                <div class="border border-gray-200 rounded-xl overflow-hidden">
                    <div class="bg-${sessionColor}-50 px-3 py-2 flex items-center gap-1.5">
                        <i class="fa-solid ${sessionIcon} text-[10px] text-${sessionColor}-500"></i>
                        <span class="text-xs font-bold text-gray-800">${escapeHtml(cls.class)}</span>
                        <span class="text-[9px] text-gray-400">(${cls.students.length} murid RMT)</span>
                    </div>

                    <!-- Summary -->
                    <div class="px-3 py-1.5">
                        <div class="flex items-center justify-between mb-1 text-[8px] font-bold text-gray-400 uppercase">
                            <span>Nama</span>
                            <div class="flex gap-2"><span class="text-emerald-500">H</span><span class="text-red-500">TH</span><span>%</span></div>
                        </div>
                        <div class="max-h-[200px] overflow-y-auto rounded border border-gray-100">
                            ${studentRows}
                        </div>
                    </div>

                    <!-- Daily grid -->
                    <div class="px-3 pb-2">
                        <p class="text-[8px] font-bold text-gray-400 uppercase mb-1">Kehadiran Harian</p>
                        <div class="rmt-daily-grid rounded border border-gray-100">
                            <table class="text-[9px]">
                                <thead><tr><th class="rmt-cell sticky left-0 bg-gray-50 text-gray-500">Nama</th>${dayHeaders}</tr></thead>
                                <tbody>${dailyRows}</tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
        });

        if (d.classes.length === 0) {
            html += `<div class="text-center py-4"><p class="text-xs text-gray-400">Tiada data RMT untuk bulan ini.</p></div>`;
        }

        container.innerHTML = html;
    } catch (err) {
        console.error("RMT load error:", err);
        container.innerHTML = `<p class="text-xs text-red-500 text-center py-4">Gagal memuatkan data RMT: ${escapeHtml(err.message)}</p>`;
    }
}

function downloadRmtExcel() {
    const month = document.getElementById("rmt-month-picker").value;
    if (!month) return;
    const cls = document.getElementById("rmt-export-class").value;
    const url = cls ? `/api/export/rmt/${month}/${encodeURIComponent(cls)}` : `/api/export/rmt/${month}`;
    window.location.href = url;
}

// ---------------------------------------------------------------------------
// Sidebar: Rekod Ketidakhadiran Tab
// ---------------------------------------------------------------------------
let _sebabData = null;      // last API payload
let _sebabFilter = "semua"; // semua | tiada_sebab | belum_disahkan | sah

const SEBAB_STATUS = {
    tiada_sebab:    { label: "Tiada Sebab",   color: "red",     icon: "fa-circle-exclamation" },
    belum_disahkan: { label: "Belum Disah",   color: "amber",   icon: "fa-clock" },
    sah:            { label: "Sah",           color: "emerald", icon: "fa-circle-check" },
    tidak_sah:      { label: "Tidak Sah",     color: "red",     icon: "fa-circle-xmark" },
};

function sebabEmptyState(msg, icon = "fa-clipboard-list") {
    return `
        <div class="text-center py-8">
            <i class="fa-solid ${icon} text-3xl text-gray-200"></i>
            <p class="text-xs text-gray-400 mt-2">${msg}</p>
        </div>`;
}

function setSebabFilter(filter) {
    _sebabFilter = filter;
    ["semua", "tiada_sebab", "belum_disahkan", "tidak_sah", "sah"].forEach(f => {
        document.getElementById(`sebab-filter-${f}`).classList.toggle("active", f === filter);
    });
    renderKetidakhadiran();
}

async function loadKetidakhadiran() {
    const className = document.getElementById("sebab-class-select").value;
    const container = document.getElementById("sebab-content");
    const controls = document.getElementById("sebab-controls");

    if (!className) {
        _sebabData = null;
        controls.classList.add("hidden");
        container.innerHTML = sebabEmptyState("Pilih kelas untuk melihat rekod ketidakhadiran.");
        return;
    }

    const from = document.getElementById("sebab-date-from").value;
    const to = document.getElementById("sebab-date-to").value;
    if (from && to && from > to) {
        controls.classList.add("hidden");
        container.innerHTML = sebabEmptyState("Tarikh mula selepas tarikh tamat.", "fa-triangle-exclamation");
        return;
    }

    container.innerHTML = `<div class="text-center py-6"><div class="w-6 h-6 border-2 border-rose-200 border-t-rose-600 rounded-full animate-spin mx-auto"></div></div>`;

    try {
        const params = new URLSearchParams();
        if (from) params.set("from", from);
        if (to) params.set("to", to);
        const qs = params.toString() ? `?${params}` : "";
        const res = await apiFetch(`/api/kelas/${encodeURIComponent(className)}/ketidakhadiran${qs}`);
        _sebabData = await res.json();

        if (_sebabData.error) {
            controls.classList.add("hidden");
            container.innerHTML = sebabEmptyState(escapeHtml(_sebabData.error), "fa-triangle-exclamation");
            return;
        }

        controls.classList.toggle("hidden", _sebabData.total_absences === 0);
        renderKetidakhadiran();
    } catch {
        controls.classList.add("hidden");
        container.innerHTML = sebabEmptyState("Gagal memuatkan rekod.", "fa-triangle-exclamation");
    }
}

function renderKetidakhadiran() {
    const container = document.getElementById("sebab-content");
    const d = _sebabData;
    if (!d) return;

    if (d.total_absences === 0) {
        container.innerHTML = sebabEmptyState(
            `Tiada ketidakhadiran direkodkan untuk ${escapeHtml(d.class)} dalam tempoh ini.`,
            "fa-face-smile"
        );
        return;
    }

    const sessionIcon = d.session === "Pagi" ? "fa-sun" : "fa-moon";
    // Server-side figure: unverified reasons still count, and a verified
    // "Ponteng" counts too — so it can't be derived from `counts` here.
    const ponteng = d.total_ponteng || 0;

    // Filter pupils by the selected status
    const pupils = _sebabFilter === "semua"
        ? d.pupils
        : d.pupils.filter(p => p.counts[_sebabFilter] > 0);

    let pupilCards = "";
    pupils.forEach((p, pi) => {
        const records = _sebabFilter === "semua"
            ? p.records
            : p.records.filter(r => r.status === _sebabFilter);
        const oi = d.pupils.indexOf(p);   // index into the unfiltered list

        let chips = "";
        records.forEach((r, ri) => {
            const meta = SEBAB_STATUS[r.status];
            const [, mm, dd] = r.date.split("-");
            const detailId = `sebab-detail-${pi}-${ri}`;
            chips += `
                <button onclick="toggleSebabDetail('${detailId}')"
                    class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-${meta.color}-100 text-${meta.color}-700 border border-${meta.color}-200 hover:bg-${meta.color}-200 transition">
                    ${dd}/${mm}
                </button>`;
        });

        let details = "";
        records.forEach((r, ri) => {
            const meta = SEBAB_STATUS[r.status];
            const detailId = `sebab-detail-${pi}-${ri}`;
            const rows = [];
            if (r.kategori) rows.push(`<div><span class="text-gray-400">Kategori:</span> ${escapeHtml(r.kategori)}</div>`);
            if (r.sub_kategori) rows.push(`<div><span class="text-gray-400">Sub-alasan:</span> ${escapeHtml(r.sub_kategori)}</div>`);
            if (r.kira_ponteng && r.status !== "tiada_sebab") rows.push(`<div class="text-red-600 font-bold"><i class="fa-solid fa-triangle-exclamation mr-0.5"></i>Dikira ponteng</div>`);
            if (r.catatan) rows.push(`<div><span class="text-gray-400">Catatan:</span> ${escapeHtml(r.catatan)}</div>`);
            if (r.jenis_bukti) rows.push(`<div><span class="text-gray-400">Bukti:</span> ${escapeHtml(r.jenis_bukti)}</div>`);
            if (r.rujukan_bukti) rows.push(`<div><span class="text-gray-400">Rujukan:</span> ${escapeHtml(r.rujukan_bukti)}</div>`);
            if (r.direkod_oleh) rows.push(`<div><span class="text-gray-400">Direkod oleh:</span> ${escapeHtml(r.direkod_oleh)}</div>`);
            if (r.sumber === "Borang Waris") {
                rows.push(`<div class="mt-1 px-1.5 py-1 rounded bg-sky-50 border border-sky-200 text-sky-700 font-bold">
                    <i class="fa-solid fa-inbox mr-0.5"></i>Daripada Waris &mdash; sila semak surat/slip asal sebelum sahkan.
                </div>`);
            }
            if (!rows.length) rows.push(`<div class="text-gray-400 italic">Belum ada sebab direkodkan.</div>`);

            details += `
                <div id="${detailId}" class="hidden bg-gray-50 border border-gray-200 rounded-lg p-2 mt-1 text-[10px] space-y-0.5">
                    <div class="flex items-center gap-1 font-bold text-${meta.color}-600 mb-1">
                        <i class="fa-solid ${meta.icon}"></i>
                        <span>${escapeHtml(r.date_label)}</span>
                        <span class="ml-auto">${meta.label}</span>
                    </div>
                    ${rows.join("")}
                    <button onclick="openSebabModal(${oi}, '${r.date}')"
                        class="mt-1 px-2 py-1 rounded text-[10px] font-bold bg-rose-500 text-white hover:bg-rose-600 transition">
                        <i class="fa-solid fa-file-pen mr-0.5"></i>${r.kategori ? "Kemas Kini" : "Rekod"} Sebab
                    </button>
                </div>`;
        });

        pupilCards += `
            <div class="border border-gray-200 rounded-lg p-2">
                <div class="flex items-start gap-1.5 mb-1.5">
                    <span class="w-4 text-[9px] text-gray-400 text-right mt-0.5">${pi + 1}</span>
                    <div class="flex-1 min-w-0">
                        <span class="text-[11px] font-bold text-gray-700 break-words">${escapeHtml(p.name)}</span>
                        ${p.still_enrolled === false ? `<span class="ml-1 px-1 py-0.5 rounded bg-slate-100 text-slate-500 text-[8px] font-bold align-middle">BEKAS MURID</span>` : ""}
                    </div>
                    <span class="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-black ${p.ponteng > 0 ? "bg-red-100 text-red-600" : "bg-gray-100 text-gray-500"}">
                        ${p.total} hari${p.ponteng > 0 ? ` &bull; ${p.ponteng} ponteng` : ""}
                    </span>
                </div>
                <div class="flex flex-wrap items-center gap-1">
                    ${chips}
                    <button onclick="openSebabModal(${oi})"
                        class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-600 border border-slate-200 hover:bg-slate-200 transition"
                        title="Rekod sebab untuk beberapa tarikh">
                        <i class="fa-solid fa-plus"></i> Sebab
                    </button>
                </div>
                ${details}
            </div>`;
    });

    if (!pupilCards) {
        pupilCards = sebabEmptyState("Tiada murid dalam tapisan ini.", "fa-filter");
    }

    container.innerHTML = `
        <!-- Class Header -->
        <div class="bg-gradient-to-r from-rose-500 to-red-600 rounded-xl p-3 text-white">
            <div class="flex items-center gap-2 mb-0.5">
                <i class="fa-solid fa-clipboard-list text-sm text-rose-200"></i>
                <span class="text-xs font-bold">${escapeHtml(d.class)}</span>
            </div>
            <span class="text-[10px] text-rose-200">
                <i class="fa-solid ${sessionIcon} mr-0.5"></i>Sesi ${d.session}
                &bull; ${d.school_days} hari rekod
                &bull; ${d.total_absences} ketidakhadiran
                &bull; ${d.total_pupils} murid
            </span>
        </div>

        <!-- Status Breakdown — one box per state, mirroring the filter chips -->
        <div class="grid grid-cols-4 gap-1.5">
            <button onclick="setSebabFilter('tiada_sebab')" class="bg-red-50 hover:bg-red-100 rounded-lg p-2 text-center transition">
                <p class="text-base font-black text-red-600">${d.counts.tiada_sebab}</p>
                <p class="text-[8px] font-bold text-red-500 leading-tight">Tiada<br>Sebab</p>
            </button>
            <button onclick="setSebabFilter('belum_disahkan')" class="bg-amber-50 hover:bg-amber-100 rounded-lg p-2 text-center transition">
                <p class="text-base font-black text-amber-600">${d.counts.belum_disahkan}</p>
                <p class="text-[8px] font-bold text-amber-500 leading-tight">Belum<br>Disahkan</p>
            </button>
            <button onclick="setSebabFilter('tidak_sah')" class="bg-red-50 hover:bg-red-100 rounded-lg p-2 text-center transition">
                <p class="text-base font-black text-red-600">${d.counts.tidak_sah}</p>
                <p class="text-[8px] font-bold text-red-500 leading-tight">Tidak<br>Sah</p>
            </button>
            <button onclick="setSebabFilter('sah')" class="bg-emerald-50 hover:bg-emerald-100 rounded-lg p-2 text-center transition">
                <p class="text-base font-black text-emerald-600">${d.counts.sah}</p>
                <p class="text-[8px] font-bold text-emerald-500 leading-tight">Sebab<br>Sah</p>
            </button>
        </div>

        ${ponteng > 0 ? `
        <div class="flex items-start gap-2 px-2.5 py-2 bg-red-50 border border-red-200 rounded-lg text-[10px] text-red-700 font-medium">
            <i class="fa-solid fa-triangle-exclamation text-red-500 mt-0.5 shrink-0"></i>
            <span><strong>${ponteng} hari</strong> masih dikira ponteng &mdash; termasuk sebab yang belum disahkan. Sahkan setelah menerima surat rasmi atau slip klinik.</span>
        </div>` : ""}

        <!-- Pupil List -->
        <div>
            <div class="flex items-center gap-1 mb-1.5">
                <i class="fa-solid fa-list-ol text-[10px] text-gray-400"></i>
                <span class="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Senarai Murid</span>
                <span class="text-[9px] text-gray-400 ml-auto">Tekan tarikh untuk butiran</span>
            </div>
            <div class="sebab-pupil-list space-y-1.5">${pupilCards}</div>
        </div>
    `;
}

function toggleSebabDetail(id) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden");
}

// --- Teacher identity (required before viewing or recording) -------------
// Authorisation lives in the server session; the password is never retained.
let _sebabTeacher = "";
let _sebabClasses = [];
let _sebabIsAdmin = false;
let _sebabOptions = null;
let _sebabTeachersLoaded = false;

function applySebabAuth(d) {
    _sebabTeacher = d.teacher || "";
    _sebabClasses = d.classes || [];
    _sebabIsAdmin = !!d.is_admin;

    const authed = !!_sebabTeacher;
    document.getElementById("sebab-auth-form").classList.toggle("hidden", authed);
    document.getElementById("sebab-auth-info").classList.toggle("hidden", !authed);
    document.getElementById("sebab-authed").classList.toggle("hidden", !authed);
    document.getElementById("amaran-tetapan-panel").classList.toggle("hidden", !_sebabIsAdmin);

    if (!authed) return;

    document.getElementById("sebab-auth-name").textContent =
        _sebabTeacher + (_sebabIsAdmin ? " (Pentadbir)" : "");

    // Only offer classes this teacher may actually open
    const opts = _sebabClasses.map(c =>
        `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    const rekodSel = document.getElementById("sebab-class-select");
    rekodSel.innerHTML = (_sebabClasses.length === 1 ? "" : '<option value="">— Pilih kelas —</option>') + opts;
    document.getElementById("amaran-class-select").innerHTML =
        `<option value="">${_sebabIsAdmin ? "Semua Kelas" : "Semua Kelas Saya"}</option>` + opts;

    // One class? Open it straight away — no reason to make them pick.
    if (_sebabClasses.length === 1) {
        rekodSel.value = _sebabClasses[0];
        loadKetidakhadiran();
    }
}

async function initSebabTab() {
    if (!_sebabOptions) {
        try {
            const res = await apiFetch("/api/sebab/options");
            _sebabOptions = await res.json();
        } catch { /* toast shown */ }
    }
    // Restore an existing session (e.g. after a page reload)
    if (!_sebabTeacher) {
        try {
            const res = await fetch("/api/settings/whoami");
            const d = await res.json();
            if (d.logged_in) applySebabAuth(d);
        } catch { /* offline */ }
    }
    if (!_sebabTeachersLoaded) {
        try {
            const res = await apiFetch("/api/settings/teachers");
            const teachers = await res.json();
            const sel = document.getElementById("sebab-auth-teacher");
            sel.innerHTML = '<option value="">— Pilih nama guru —</option>';
            teachers.forEach(t => {
                sel.innerHTML += `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`;
            });
            _sebabTeachersLoaded = true;
        } catch { /* toast shown */ }
    }
}

async function sebabLogin() {
    const teacher = document.getElementById("sebab-auth-teacher").value;
    const password = document.getElementById("sebab-auth-password").value;
    const err = document.getElementById("sebab-auth-error");

    const fail = msg => {
        err.textContent = msg;
        err.classList.remove("hidden");
    };
    if (!teacher) return fail("Sila pilih nama guru.");
    if (!password) return fail("Sila masukkan kata laluan.");

    try {
        const res = await fetch("/api/settings/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ teacher, password }),
        });
        const d = await res.json();
        if (!d.success) return fail(d.error || "Kata laluan salah.");

        err.classList.add("hidden");
        document.getElementById("sebab-auth-password").value = "";
        applySebabAuth(d);

        if (!d.is_admin && (!d.classes || !d.classes.length)) {
            fail("Anda belum ditetapkan sebagai guru kelas. Sila hubungi pentadbir sistem.");
            document.getElementById("sebab-authed").classList.add("hidden");
        }
    } catch {
        fail("Ralat sambungan.");
    }
}

async function sebabLogout() {
    try { await fetch("/api/settings/logout", { method: "POST" }); } catch { /* ignore */ }
    _amaranData = null;
    _sebabData = null;
    applySebabAuth({});
}

// --- Rekod Sebab modal ---------------------------------------------------
let _sebabModalPupil = null;

function openSebabModal(pupilIndex, presetDate) {
    if (!_sebabTeacher) {
        showToast("Sila kenal pasti diri anda dahulu untuk merekod sebab.", "error");
        document.getElementById("sebab-auth-teacher").focus();
        return;
    }
    const p = _sebabData && _sebabData.pupils[pupilIndex];
    if (!p) return;
    _sebabModalPupil = p;

    document.getElementById("sebab-modal-name").textContent = p.name;
    document.getElementById("sebab-modal-class").textContent =
        `${_sebabData.class} • ${p.total} hari tidak hadir • ${p.ponteng} tiada sebab sah`;

    // Date checkboxes — newest first, preset one ticked if given
    const box = document.getElementById("sebab-modal-dates");
    box.innerHTML = p.records.map((r, i) => {
        const meta = SEBAB_STATUS[r.status];
        const checked = presetDate && r.date === presetDate ? "checked" : "";
        return `
            <label class="flex items-center gap-1.5 px-1.5 py-1 rounded hover:bg-gray-50 cursor-pointer">
                <input type="checkbox" class="sebab-date-cb accent-rose-500" value="${r.date}" ${checked}>
                <span class="text-[11px] text-gray-700 flex-1">${escapeHtml(r.date_label)}</span>
                <span class="text-[9px] font-bold text-${meta.color}-600">${meta.label}</span>
            </label>`;
    }).join("");

    // Options
    const opts = _sebabOptions || { kategori: [], jenis_bukti: [], status_sah: [] };
    const kSel = document.getElementById("sebab-modal-kategori");
    kSel.innerHTML = opts.kategori
        .map(k => `<option value="${escapeHtml(k.label)}">${escapeHtml(k.label)}</option>`).join("");
    kSel.onchange = onSebabKategoriChange;

    const bSel = document.getElementById("sebab-modal-bukti");
    bSel.innerHTML = '<option value="">— Tiada —</option>' +
        opts.jenis_bukti.map(b => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join("");

    const sSel = document.getElementById("sebab-modal-status");
    sSel.innerHTML = opts.status_sah
        .map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");

    // Prefill from the existing entry for the preset date, if any
    const existing = presetDate ? p.records.find(r => r.date === presetDate) : null;
    if (existing && existing.kategori) {
        kSel.value = existing.kategori;
        populateSebabSub(existing.sub_kategori || "");
        bSel.value = existing.jenis_bukti || "";
        sSel.value = existing.status_sah || "";
        document.getElementById("sebab-modal-catatan").value = existing.catatan || "";
        document.getElementById("sebab-modal-rujukan").value = existing.rujukan_bukti || "";
    } else {
        document.getElementById("sebab-modal-catatan").value = "";
        document.getElementById("sebab-modal-rujukan").value = "";
    }
    if (!existing || !existing.kategori) populateSebabSub("");
    updateSebabKategoriHint();

    document.getElementById("sebab-modal-error").classList.add("hidden");
    document.getElementById("sebab-modal").classList.remove("hidden");
}

function populateSebabSub(selected) {
    const kategori = document.getElementById("sebab-modal-kategori").value;
    const k = ((_sebabOptions && _sebabOptions.kategori) || []).find(x => x.label === kategori);
    const sel = document.getElementById("sebab-modal-sub");
    const subs = (k && k.sub) || [];
    sel.innerHTML = (subs.length > 1 ? '<option value="">— Pilih sub-alasan —</option>' : "") +
        subs.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    if (selected && subs.indexOf(selected) !== -1) sel.value = selected;
}

function onSebabKategoriChange() {
    populateSebabSub("");
    updateSebabKategoriHint();
}

function updateSebabKategoriHint() {
    const val = document.getElementById("sebab-modal-kategori").value;
    const hint = document.getElementById("sebab-modal-kategori-hint");
    const k = ((_sebabOptions && _sebabOptions.kategori) || []).find(x => x.label === val);
    if (!k) { hint.textContent = ""; return; }
    if (k.kira_ponteng) {
        hint.className = "text-[10px] mt-1 text-red-600 font-bold";
        hint.innerHTML = `<i class="fa-solid fa-triangle-exclamation mr-0.5"></i>Dikira sebagai ponteng walaupun disahkan — menyumbang kepada surat amaran.`;
    } else {
        hint.className = "text-[10px] mt-1 text-emerald-600 font-medium";
        hint.innerHTML = `<i class="fa-solid fa-circle-check mr-0.5"></i>Tidak dikira sebagai ponteng.`;
    }
}

function toggleAllSebabDates() {
    const cbs = [...document.querySelectorAll(".sebab-date-cb")];
    const allOn = cbs.every(cb => cb.checked);
    cbs.forEach(cb => { cb.checked = !allOn; });
    document.getElementById("sebab-modal-toggle-all").textContent =
        allOn ? "Pilih Semua" : "Kosongkan";
}

function closeSebabModal() {
    document.getElementById("sebab-modal").classList.add("hidden");
    _sebabModalPupil = null;
}

async function submitSebab() {
    const err = document.getElementById("sebab-modal-error");
    const btn = document.getElementById("sebab-modal-save");
    const fail = msg => {
        err.textContent = msg;
        err.classList.remove("hidden");
        btn.disabled = false;
    };

    const dates = [...document.querySelectorAll(".sebab-date-cb")]
        .filter(cb => cb.checked).map(cb => cb.value);
    if (!dates.length) return fail("Sila pilih sekurang-kurangnya satu tarikh.");
    if (!_sebabModalPupil) return fail("Ralat: murid tidak dikenal pasti.");

    btn.disabled = true;
    err.classList.add("hidden");

    try {
        const res = await fetch("/api/sebab", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                class: _sebabData.class,
                name: _sebabModalPupil.name,
                dates,
                kategori: document.getElementById("sebab-modal-kategori").value,
                sub_kategori: document.getElementById("sebab-modal-sub").value,
                catatan: document.getElementById("sebab-modal-catatan").value,
                jenis_bukti: document.getElementById("sebab-modal-bukti").value,
                rujukan_bukti: document.getElementById("sebab-modal-rujukan").value,
                status_sah: document.getElementById("sebab-modal-status").value,
            }),
        });
        const d = await res.json();
        if (!d.success) {
            if (res.status === 403) sebabLogout();
            return fail(d.error || "Gagal menyimpan rekod.");
        }

        closeSebabModal();
        showToast(`${d.recorded} rekod sebab disimpan.`, "success");
        await loadKetidakhadiran();
    } catch {
        fail("Ralat sambungan.");
    } finally {
        btn.disabled = false;
    }
}

// --- Hantar ke Group Kelas ----------------------------------------------
let _borangData = null;

function openBorangModal() {
    const cls = document.getElementById("sebab-class-select").value;
    if (!cls) {
        showToast("Sila pilih kelas dahulu.", "error");
        return;
    }
    // Leave the date empty so the server picks the latest date that actually
    // has absences — "today" is often a weekend or holiday.
    document.getElementById("borang-date").value = "";
    document.getElementById("borang-date").max = new Date().toISOString().slice(0, 10);
    document.getElementById("borang-error").classList.add("hidden");
    document.getElementById("borang-modal").classList.remove("hidden");
    loadBorangMessage();
}

function pickBorangDate(date) {
    document.getElementById("borang-date").value = date;
    loadBorangMessage();
}

function renderBorangDates() {
    const d = _borangData;
    const box = document.getElementById("borang-dates");
    const counter = document.getElementById("borang-dates-count");
    const dates = (d && d.dates) || [];

    counter.textContent = dates.length ? `${dates.length} tarikh` : "";
    if (!dates.length) {
        box.innerHTML = `<div class="px-2 py-3 text-center text-[10px] text-gray-400">
            Tiada ketidakhadiran direkodkan bagi kelas ini.</div>`;
        return;
    }
    box.innerHTML = dates.map(x => {
        const active = x.date === d.date;
        return `<button onclick="pickBorangDate('${x.date}')"
            class="w-full flex items-center gap-2 px-2 py-1.5 text-left transition ${active ? "bg-emerald-50" : "hover:bg-gray-50"}">
            <i class="fa-solid ${active ? "fa-circle-check text-emerald-600" : "fa-calendar-day text-gray-300"} text-[10px] shrink-0"></i>
            <span class="text-[11px] ${active ? "font-bold text-emerald-700" : "text-gray-700"} flex-1 truncate">${escapeHtml(x.label)}</span>
            <span class="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-black bg-red-100 text-red-600">${x.count}</span>
        </button>`;
    }).join("");
}

function closeBorangModal() {
    document.getElementById("borang-modal").classList.add("hidden");
}

async function loadBorangMessage() {
    const cls = document.getElementById("sebab-class-select").value;
    const date = document.getElementById("borang-date").value;
    const err = document.getElementById("borang-error");
    const box = document.getElementById("borang-message");
    box.value = "Memuatkan...";

    try {
        const res = await apiFetch(`/api/kelas/${encodeURIComponent(cls)}/borang?date=${encodeURIComponent(date)}`);
        _borangData = await res.json();
        if (_borangData.error) {
            err.textContent = _borangData.error;
            err.classList.remove("hidden");
            box.value = "";
            return;
        }
        err.classList.add("hidden");
        box.value = _borangData.message;
        document.getElementById("borang-date").value = _borangData.date;

        let ringkasan;
        if (_borangData.absent_count) {
            ringkasan = `<i class="fa-solid fa-user-xmark text-red-500 mr-1"></i><strong>${_borangData.absent_count}</strong> murid tidak hadir pada ${escapeHtml(_borangData.date_label)}.`;
        } else if (_borangData.recorded) {
            ringkasan = `<i class="fa-solid fa-face-smile text-emerald-500 mr-1"></i>Semua murid hadir pada ${escapeHtml(_borangData.date_label)}.`;
        } else {
            ringkasan = `<i class="fa-solid fa-circle-info text-amber-500 mr-1"></i>Tiada rekod kehadiran pada ${escapeHtml(_borangData.date_label)} — cuti atau belum diisi.`;
        }
        document.getElementById("borang-summary").innerHTML = ringkasan;
        renderBorangDates();
    } catch {
        box.value = "";
        err.textContent = "Gagal memuatkan mesej.";
        err.classList.remove("hidden");
    }
}

async function copyBorangMessage() {
    if (!_borangData) return;
    const btn = document.getElementById("borang-copy-btn");
    try {
        await navigator.clipboard.writeText(_borangData.message);
    } catch {
        // Older WebViews: fall back to selecting the textarea
        const box = document.getElementById("borang-message");
        box.removeAttribute("readonly");
        box.select();
        document.execCommand("copy");
        box.setAttribute("readonly", "readonly");
    }
    btn.innerHTML = '<i class="fa-solid fa-check mr-1"></i>Disalin!';
    setTimeout(() => { btn.innerHTML = '<i class="fa-solid fa-copy mr-1"></i>Salin Mesej'; }, 1800);
}

function openWhatsApp() {
    if (!_borangData) return;
    window.open(`https://wa.me/?text=${encodeURIComponent(_borangData.message)}`, "_blank");
}

async function resetBorangLink() {
    const cls = document.getElementById("sebab-class-select").value;
    if (!cls) return;
    if (!confirm(`Jana pautan baharu untuk ${cls}?\n\nPautan lama yang telah dihantar ke group akan TIDAK BOLEH digunakan lagi.`)) return;
    try {
        const res = await fetch(`/api/kelas/${encodeURIComponent(cls)}/borang/jana-semula`, { method: "POST" });
        const d = await res.json();
        if (!d.success) {
            showToast(d.error || "Gagal menjana pautan baharu.", "error");
            return;
        }
        showToast("Pautan baharu dijana. Sila hantar semula ke group.", "success");
        await loadBorangMessage();
    } catch {
        showToast("Ralat sambungan.", "error");
    }
}

// --- Surat Amaran --------------------------------------------------------
let _amaranData = null;
let _amaranTetapan = null;

function switchSebabView(view) {
    ["rekod", "amaran"].forEach(v => {
        document.getElementById(`sebab-view-${v}`).classList.toggle("hidden", v !== view);
        document.getElementById(`sebab-view-btn-${v}`).classList.toggle("active", v === view);
    });
    if (view === "amaran") {
        if (!_amaranTetapan) loadAmaranTetapan();
        if (!_amaranData) loadAmaran();
    }
}

async function loadAmaranTetapan() {
    try {
        const res = await apiFetch("/api/amaran/tetapan");
        _amaranTetapan = await res.json();
        const box = document.getElementById("amaran-tetapan-rows");
        box.innerHTML = Object.keys(_amaranTetapan.thresholds).sort().map(t => `
            <div class="flex items-center gap-1.5">
                <span class="text-[10px] font-bold text-gray-500 w-4">T${t}</span>
                <span class="text-[10px] text-gray-600 flex-1 truncate">${escapeHtml(_amaranTetapan.labels[t] || "")}</span>
                <input type="number" min="1" max="365" id="amaran-t-${t}" value="${_amaranTetapan.thresholds[t]}"
                    class="w-14 border border-gray-300 rounded px-1.5 py-1 text-[11px] text-center focus:ring-2 focus:ring-rose-400 outline-none">
                <span class="text-[10px] text-gray-400">hari</span>
            </div>`).join("");
    } catch { /* toast shown */ }
}

async function saveAmaranTetapan() {
    const err = document.getElementById("amaran-tetapan-error");
    const fail = msg => { err.textContent = msg; err.classList.remove("hidden"); };
    if (!_sebabTeacher) return fail("Sila kenal pasti diri anda dahulu.");

    const thresholds = {};
    Object.keys(_amaranTetapan.thresholds).forEach(t => {
        thresholds[t] = document.getElementById(`amaran-t-${t}`).value;
    });

    try {
        const res = await fetch("/api/amaran/tetapan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thresholds }),
        });
        const d = await res.json();
        if (!d.success) {
            if (res.status === 403) sebabLogout();
            return fail(d.error || "Gagal menyimpan paras.");
        }
        err.classList.add("hidden");
        showToast("Paras amaran dikemas kini.", "success");
        _amaranTetapan = null;
        await loadAmaranTetapan();
        await loadAmaran();
    } catch {
        fail("Ralat sambungan.");
    }
}

async function loadAmaran() {
    const container = document.getElementById("amaran-content");
    const cls = document.getElementById("amaran-class-select").value;
    container.innerHTML = `<div class="text-center py-6"><div class="w-6 h-6 border-2 border-rose-200 border-t-rose-600 rounded-full animate-spin mx-auto"></div></div>`;
    try {
        const qs = cls ? `?class=${encodeURIComponent(cls)}` : "";
        const res = await apiFetch(`/api/amaran/senarai${qs}`);
        _amaranData = await res.json();
        if (_amaranData.error) {
            container.innerHTML = sebabEmptyState(escapeHtml(_amaranData.error), "fa-triangle-exclamation");
            return;
        }
        renderAmaran();
    } catch {
        container.innerHTML = sebabEmptyState("Gagal memuatkan senarai amaran.", "fa-triangle-exclamation");
    }
}

function renderAmaran() {
    const container = document.getElementById("amaran-content");
    const d = _amaranData;
    if (!d) return;

    if (!d.pupils.length) {
        container.innerHTML = sebabEmptyState(
            "Tiada murid mencapai paras amaran. Rekod kehadiran sekolah dalam keadaan baik.",
            "fa-face-smile"
        );
        return;
    }

    let cards = "";
    d.pupils.forEach((p, i) => {
        const urgent = p.perlu_tindakan;
        const color = urgent ? "red" : "gray";
        const issued = p.tahap_dikeluarkan > 0
            ? `<div class="text-[9px] text-gray-500 mt-1">
                   <i class="fa-solid fa-check text-emerald-500 mr-0.5"></i>
                   ${escapeHtml(d.labels[p.tahap_dikeluarkan] || "")} dikeluarkan ${escapeHtml(p.amaran_terakhir)}
                   ${p.amaran_oleh ? `oleh ${escapeHtml(p.amaran_oleh)}` : ""}
               </div>`
            : `<div class="text-[9px] text-amber-600 mt-1"><i class="fa-solid fa-circle-exclamation mr-0.5"></i>Belum ada surat dikeluarkan</div>`;

        cards += `
            <div class="border ${urgent ? "border-red-300 bg-red-50/40" : "border-gray-200"} rounded-lg p-2">
                <div class="flex items-start gap-1.5">
                    <span class="w-4 text-[9px] text-gray-400 text-right mt-0.5">${i + 1}</span>
                    <div class="flex-1 min-w-0">
                        <span class="text-[11px] font-bold text-gray-800 break-words">${escapeHtml(p.name)}</span>
                        ${p.still_enrolled === false ? `<span class="ml-1 px-1 py-0.5 rounded bg-slate-100 text-slate-500 text-[8px] font-bold align-middle">BEKAS MURID</span>` : ""}
                        <div class="text-[9px] text-gray-500">${escapeHtml(p.class)}</div>
                    </div>
                    <span class="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-black bg-${color}-100 text-${color}-700">
                        ${p.ponteng} ponteng
                    </span>
                </div>
                <div class="mt-1.5 px-1.5 py-1 rounded bg-${color}-100 text-${color}-700 text-[10px] font-bold text-center">
                    Layak: ${escapeHtml(p.tahap_label)}
                </div>
                ${issued}
                <div class="flex gap-1 mt-1.5">
                    <button onclick="printSurat(${i})" class="flex-1 px-2 py-1.5 rounded text-[10px] font-bold bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 transition">
                        <i class="fa-solid fa-print mr-0.5"></i>Cetak Surat
                    </button>
                    <button onclick="markAmaranIssued(${i})" ${urgent ? "" : "disabled"}
                        class="flex-1 px-2 py-1.5 rounded text-[10px] font-bold bg-rose-500 text-white hover:bg-rose-600 transition disabled:opacity-40 disabled:cursor-not-allowed">
                        <i class="fa-solid fa-stamp mr-0.5"></i>Tanda Dikeluarkan
                    </button>
                </div>
            </div>`;
    });

    container.innerHTML = `
        <div class="grid grid-cols-2 gap-1.5 mb-2">
            <div class="bg-red-50 rounded-lg p-2 text-center">
                <p class="text-lg font-black text-red-600">${d.perlu_tindakan}</p>
                <p class="text-[9px] font-bold text-red-500">Perlu Tindakan</p>
            </div>
            <div class="bg-gray-50 rounded-lg p-2 text-center">
                <p class="text-lg font-black text-gray-700">${d.pupils.length}</p>
                <p class="text-[9px] font-bold text-gray-500">Capai Paras Amaran</p>
            </div>
        </div>
        <div class="amaran-list space-y-1.5">${cards}</div>`;
}

function printSurat(i) {
    const p = _amaranData && _amaranData.pupils[i];
    if (!p) return;
    const url = `/surat-amaran/${encodeURIComponent(p.class)}/${encodeURIComponent(p.name)}?tahap=${p.tahap_layak}`;
    window.open(url, "_blank");
}

async function markAmaranIssued(i) {
    if (!_sebabTeacher) {
        showToast("Sila kenal pasti diri anda dahulu.", "error");
        return;
    }
    const p = _amaranData && _amaranData.pupils[i];
    if (!p) return;
    if (!confirm(`Sahkan ${_amaranData.labels[p.tahap_layak]} telah dikeluarkan kepada ${p.name}?`)) return;

    try {
        const res = await fetch("/api/amaran/rekod", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                class: p.class, name: p.name,
                tahap: p.tahap_layak, jumlah_ponteng: p.ponteng,
            }),
        });
        const d = await res.json();
        if (!d.success) {
            if (res.status === 403) sebabLogout();
            showToast(d.error || "Gagal merekod surat amaran.", "error");
            return;
        }
        showToast("Surat amaran direkodkan.", "success");
        await loadAmaran();
    } catch {
        showToast("Ralat sambungan.", "error");
    }
}

function downloadKetidakhadiran() {
    const className = document.getElementById("sebab-class-select").value;
    if (!className) return;
    const from = document.getElementById("sebab-date-from").value;
    const to = document.getElementById("sebab-date-to").value;
    const params = new URLSearchParams();
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    const qs = params.toString() ? `?${params}` : "";
    window.location.href = `/api/export/ketidakhadiran/${encodeURIComponent(className)}${qs}`;
}

// ---------------------------------------------------------------------------
// Settings Modal: Login + Student Management
// ---------------------------------------------------------------------------
let _settingsMode = "add";
let _settingsTeacher = "";

function openSettings() {
    // Show login modal first
    const loginModal = document.getElementById("settings-login-modal");
    loginModal.classList.remove("hidden");
    document.getElementById("settings-login-error").classList.add("hidden");
    document.getElementById("settings-login-password").value = "";

    // Load teacher list
    const sel = document.getElementById("settings-login-teacher");
    apiFetch("/api/settings/teachers").then(r => r.json()).then(teachers => {
        sel.innerHTML = '<option value="">— Pilih nama anda —</option>';
        teachers.forEach(t => {
            sel.innerHTML += `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`;
        });
    }).catch(() => {});
}

function closeSettingsLogin() {
    document.getElementById("settings-login-modal").classList.add("hidden");
}

async function submitSettingsLogin() {
    const teacher = document.getElementById("settings-login-teacher").value;
    const password = document.getElementById("settings-login-password").value;
    const errDiv = document.getElementById("settings-login-error");

    if (!teacher) {
        errDiv.textContent = "Sila pilih nama guru.";
        errDiv.classList.remove("hidden");
        return;
    }
    if (!password) {
        errDiv.textContent = "Sila masukkan kata laluan.";
        errDiv.classList.remove("hidden");
        return;
    }

    try {
        const res = await fetch("/api/settings/login", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({teacher, password}),
        });
        const d = await res.json();

        if (d.success) {
            _settingsTeacher = teacher;
            closeSettingsLogin();
            openSettingsPanel();
        } else {
            errDiv.textContent = d.error || "Kata laluan salah.";
            errDiv.classList.remove("hidden");
        }
    } catch {
        errDiv.textContent = "Ralat sambungan.";
        errDiv.classList.remove("hidden");
    }
}

function openSettingsPanel() {
    const modal = document.getElementById("settings-modal");
    modal.classList.remove("hidden");
    document.getElementById("settings-teacher-label").textContent = _settingsTeacher;

    // Populate class dropdown
    const sel = document.getElementById("settings-class");
    const currentVal = sel.value;
    apiFetch("/api/classes").then(r => r.json()).then(classes => {
        sel.innerHTML = '<option value="">— Pilih kelas —</option>';
        classes.forEach(c => {
            sel.innerHTML += `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`;
        });
        if (currentVal) sel.value = currentVal;
    }).catch(() => {});

    // Enable/disable add button based on name input
    const nameInput = document.getElementById("settings-add-name");
    nameInput.addEventListener("input", () => {
        document.getElementById("settings-add-btn").disabled =
            !nameInput.value.trim() || !document.getElementById("settings-class").value;
    });
}

function closeSettings() {
    document.getElementById("settings-modal").classList.add("hidden");
    _settingsTeacher = "";
}

function switchSettingsMode(mode) {
    _settingsMode = mode;
    ["add", "remove", "notif", "guru"].forEach(m => {
        const btn = document.getElementById(`settings-tab-${m}`);
        const content = document.getElementById(`settings-mode-${m}`);
        if (m === mode) {
            btn.classList.add("active");
            content.classList.remove("hidden");
        } else {
            btn.classList.remove("active");
            content.classList.add("hidden");
        }
    });

    // Hide class selector for modes that don't use it
    const classWrapper = document.getElementById("settings-class-wrapper");
    if (classWrapper) {
        classWrapper.classList.toggle("hidden", mode === "notif" || mode === "guru");
    }

    if (mode === "notif") {
        loadNotifikasiStatus();
    } else if (mode === "guru") {
        loadGuruList();
    } else {
        loadSettingsStudents();
    }
}

// --- Class-teacher assignment -------------------------------------------
let _guruData = null;

async function loadGuruList() {
    try {
        const res = await apiFetch("/api/settings/guru");
        _guruData = await res.json();
    } catch {
        return;
    }

    document.getElementById("guru-no-admin-warning")
        .classList.toggle("hidden", !!_guruData.has_admin);

    const sel = document.getElementById("guru-select");
    sel.innerHTML = '<option value="">— Pilih guru —</option>' +
        _guruData.teachers.map(t =>
            `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`).join("");

    const saveBtn = document.getElementById("guru-save-btn");
    saveBtn.disabled = !_guruData.can_manage;
    saveBtn.classList.toggle("opacity-50", !_guruData.can_manage);

    document.getElementById("guru-current-list").innerHTML =
        _guruData.teachers.map(t => {
            const label = t.is_admin
                ? '<span class="text-indigo-600 font-bold">Semua Kelas (Pentadbir)</span>'
                : (t.classes.length
                    ? escapeHtml(t.classes.join(", "))
                    : '<span class="text-amber-600">Belum ditetapkan</span>');
            return `<div class="flex items-start gap-2 px-2 py-1.5 text-[10px]">
                        <span class="font-semibold text-gray-700 flex-1 min-w-0 break-words">${escapeHtml(t.name)}</span>
                        <span class="text-gray-500 text-right shrink-0 max-w-[55%] break-words">${label}</span>
                    </div>`;
        }).join("");
}

function onGuruSelect() {
    const name = document.getElementById("guru-select").value;
    const wrapper = document.getElementById("guru-assign-wrapper");
    document.getElementById("guru-error").classList.add("hidden");

    if (!name || !_guruData) {
        wrapper.classList.add("hidden");
        return;
    }
    const t = _guruData.teachers.find(x => x.name === name);
    if (!t) { wrapper.classList.add("hidden"); return; }

    document.getElementById("guru-is-admin").checked = t.is_admin;
    document.getElementById("guru-class-list").innerHTML =
        _guruData.all_classes.map(c => `
            <label class="flex items-center gap-1.5 px-1.5 py-1 rounded hover:bg-gray-50 cursor-pointer">
                <input type="checkbox" class="guru-class-cb accent-indigo-600" value="${escapeHtml(c)}"
                    ${t.classes.indexOf(c) !== -1 ? "checked" : ""}>
                <span class="text-[11px] text-gray-700">${escapeHtml(c)}</span>
            </label>`).join("");

    wrapper.classList.remove("hidden");
    onGuruAdminToggle();
}

function onGuruAdminToggle() {
    const isAdmin = document.getElementById("guru-is-admin").checked;
    document.getElementById("guru-class-list-wrapper").classList.toggle("hidden", isAdmin);
}

async function saveGuru() {
    const err = document.getElementById("guru-error");
    const fail = msg => { err.textContent = msg; err.classList.remove("hidden"); };
    const teacher = document.getElementById("guru-select").value;
    if (!teacher) return fail("Sila pilih guru.");

    const isAdmin = document.getElementById("guru-is-admin").checked;
    const classes = isAdmin
        ? "SEMUA"
        : [...document.querySelectorAll(".guru-class-cb")].filter(cb => cb.checked).map(cb => cb.value);

    try {
        const res = await fetch("/api/settings/guru", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ teacher, classes }),
        });
        const d = await res.json();
        if (!d.success) return fail(d.error || "Gagal menyimpan tetapan.");
        err.classList.add("hidden");
        showToast(`Tetapan guru kelas dikemas kini: ${teacher}.`, "success");
        await loadGuruList();
        document.getElementById("guru-select").value = teacher;
        onGuruSelect();
    } catch {
        fail("Ralat sambungan.");
    }
}

async function loadNotifikasiStatus() {
    const toggle = document.getElementById("notif-toggle");
    const label = document.getElementById("notif-status-label");
    label.textContent = "Memuatkan...";
    toggle.disabled = true;
    try {
        const res = await apiFetch("/api/settings/notifikasi");
        const d = await res.json();
        toggle.checked = !!d.notifikasi;
        label.textContent = d.notifikasi ? "Status: HIDUP — auto aktif" : "Status: MATI — auto dimatikan";
        toggle.disabled = false;
    } catch {
        label.textContent = "Gagal memuatkan status.";
    }
}

async function toggleNotifikasi() {
    const toggle = document.getElementById("notif-toggle");
    const label = document.getElementById("notif-status-label");
    const enabled = toggle.checked;
    const previous = !enabled;

    const confirmMsg = enabled
        ? "Hidupkan notifikasi auto Telegram?\n\nPeringatan dan rumusan akan dihantar mengikut jadual."
        : "Matikan notifikasi auto Telegram?\n\nPeringatan dan rumusan TIDAK akan dihantar sehingga dihidupkan semula.";
    if (!confirm(confirmMsg)) {
        toggle.checked = previous;
        return;
    }

    toggle.disabled = true;
    label.textContent = "Mengemaskini...";
    try {
        const res = await apiFetch("/api/settings/notifikasi", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled, teacher: _settingsTeacher}),
        });
        const d = await res.json();
        if (d.success) {
            label.textContent = enabled ? "Status: HIDUP — auto aktif" : "Status: MATI — auto dimatikan";
            showToast(`Notifikasi auto Telegram telah ${enabled ? 'DIHIDUPKAN' : 'DIMATIKAN'}.`, "success");
        } else {
            toggle.checked = previous;
            label.textContent = "Gagal mengemaskini.";
        }
    } catch {
        toggle.checked = previous;
        label.textContent = "Gagal mengemaskini.";
    } finally {
        toggle.disabled = false;
    }
}

async function loadSettingsStudents() {
    const cls = document.getElementById("settings-class").value;
    const addList = document.getElementById("settings-add-list");
    const removeList = document.getElementById("settings-remove-list");
    const addBtn = document.getElementById("settings-add-btn");

    addBtn.disabled = true;

    if (!cls) {
        const emptyMsg = '<p class="text-xs text-gray-300 text-center py-4">Pilih kelas untuk melihat senarai.</p>';
        addList.innerHTML = emptyMsg;
        removeList.innerHTML = emptyMsg;
        return;
    }

    try {
        const res = await apiFetch(`/api/students/${encodeURIComponent(cls)}`);
        const students = await res.json();

        // Add mode — show reference list with RMT badge + toggle
        let addHtml = "";
        students.forEach((s, i) => {
            const rmtBadge = s.is_rmt
                ? '<span class="text-[9px] font-bold text-orange-600 bg-orange-100 px-1 py-0.5 rounded">RMT</span>'
                : '';
            const rmtToggle = s.is_rmt
                ? `<button onclick="toggleRmt('${escapeHtml(s.name)}', false)" class="text-[9px] font-bold text-red-500 hover:text-red-700 px-1.5 py-0.5 rounded hover:bg-red-50 transition" title="Buang dari RMT">Buang RMT</button>`
                : `<button onclick="toggleRmt('${escapeHtml(s.name)}', true)" class="text-[9px] font-bold text-orange-500 hover:text-orange-700 px-1.5 py-0.5 rounded hover:bg-orange-50 transition" title="Set sebagai RMT">Set RMT</button>`;
            addHtml += `
                <div class="settings-student-row">
                    <div class="flex items-center gap-1.5 min-w-0 flex-1">
                        <span class="text-[9px] text-gray-400 w-4 text-right shrink-0">${i + 1}</span>
                        <span class="font-semibold text-gray-700 truncate">${escapeHtml(s.name)}</span>
                        ${rmtBadge}
                    </div>
                    <div class="shrink-0">${rmtToggle}</div>
                </div>`;
        });
        if (!students.length) addHtml = '<p class="text-xs text-gray-300 text-center py-4">Tiada murid dalam kelas ini.</p>';
        addList.innerHTML = addHtml;

        // Remove mode — show list with delete buttons
        let removeHtml = "";
        students.forEach((s, i) => {
            const rmtBadge = s.is_rmt
                ? '<span class="text-[9px] font-bold text-orange-600 bg-orange-100 px-1 py-0.5 rounded">RMT</span>'
                : '';
            removeHtml += `
                <div class="settings-student-row">
                    <div class="flex items-center gap-1.5 min-w-0 flex-1">
                        <span class="text-[9px] text-gray-400 w-4 text-right shrink-0">${i + 1}</span>
                        <span class="font-semibold text-gray-700 truncate">${escapeHtml(s.name)}</span>
                        ${rmtBadge}
                    </div>
                    <button onclick="removeStudent('${escapeHtml(s.name)}', '${escapeHtml(cls)}')"
                        class="shrink-0 w-7 h-7 rounded-lg bg-red-50 hover:bg-red-100 flex items-center justify-center transition" title="Padam">
                        <i class="fa-solid fa-trash-can text-[10px] text-red-500"></i>
                    </button>
                </div>`;
        });
        if (!students.length) removeHtml = '<p class="text-xs text-gray-300 text-center py-4">Tiada murid dalam kelas ini.</p>';
        removeList.innerHTML = removeHtml;

        // Enable add button if name has value
        const nameVal = document.getElementById("settings-add-name").value.trim();
        addBtn.disabled = !nameVal;
    } catch {
        addList.innerHTML = '<p class="text-xs text-red-400 text-center py-4">Gagal memuatkan data.</p>';
        removeList.innerHTML = '<p class="text-xs text-red-400 text-center py-4">Gagal memuatkan data.</p>';
    }
}

async function addStudent() {
    const cls = document.getElementById("settings-class").value;
    const nameInput = document.getElementById("settings-add-name");
    const name = nameInput.value.trim().toUpperCase();
    const isRmt = document.getElementById("settings-add-rmt").checked;

    if (!cls || !name) {
        showToast("Sila pilih kelas dan masukkan nama murid.", "error");
        return;
    }

    try {
        const res = await apiFetch("/api/students/add", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name, class: cls, is_rmt: isRmt, teacher: _settingsTeacher}),
        });
        const d = await res.json();
        if (d.success) {
            showToast(`${name} berjaya ditambah ke ${cls}${isRmt ? ' (RMT)' : ''}.`, "success");
            nameInput.value = "";
            document.getElementById("settings-add-rmt").checked = false;
            loadSettingsStudents();
            // Refresh main class dropdowns
            loadClasses();
        }
    } catch { /* toast shown by apiFetch */ }
}

async function removeStudent(name, cls) {
    if (!confirm(`Padam ${name} dari kelas ${cls}?\n\nRekod kehadiran lama TIDAK akan terjejas.`)) return;

    try {
        const res = await apiFetch("/api/students/remove", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name, class: cls, teacher: _settingsTeacher}),
        });
        const d = await res.json();
        if (d.success) {
            showToast(`${name} telah dipadamkan dari ${cls}.`, "success");
            loadSettingsStudents();
            loadClasses();
        }
    } catch { /* toast shown */ }
}

async function toggleRmt(name, setRmt) {
    const action = setRmt ? "set sebagai murid RMT" : "buang dari senarai RMT";
    if (!confirm(`${name}\n\n${setRmt ? 'Set' : 'Buang'} sebagai murid RMT?`)) return;

    try {
        const res = await apiFetch("/api/students/update-rmt", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name, is_rmt: setRmt, teacher: _settingsTeacher}),
        });
        const d = await res.json();
        if (d.success) {
            showToast(`${name} ${setRmt ? 'kini murid RMT' : 'bukan lagi murid RMT'}.`, "success");
            loadSettingsStudents();
        }
    } catch { /* toast shown */ }
}


// ---------------------------------------------------------------------------
// Utility: Format date
// ---------------------------------------------------------------------------
function formatDate(dateStr) {
    const months = ["Jan","Feb","Mac","Apr","Mei","Jun","Jul","Ogo","Sep","Okt","Nov","Dis"];
    const parts = dateStr.split("-");
    if (parts.length === 3) {
        return `${parseInt(parts[2])} ${months[parseInt(parts[1]) - 1]} ${parts[0]}`;
    }
    return dateStr;
}
