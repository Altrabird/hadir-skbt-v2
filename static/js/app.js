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

function toggleSidebar() {
    const panel = document.getElementById("sidebar-panel");
    const overlay = document.getElementById("sidebar-overlay");
    const toggleBtn = document.getElementById("sidebar-toggle-btn");

    sidebarOpen = !sidebarOpen;
    panel.classList.toggle("open", sidebarOpen);
    overlay.classList.toggle("hidden", !sidebarOpen);
    toggleBtn.classList.toggle("hidden", sidebarOpen);

    if (sidebarOpen && !sidebarStudentsList) {
        loadSidebarData();
    }
}

async function loadSidebarData() {
    try {
        const res = await apiFetch("/api/summary/students-list");
        sidebarStudentsList = await res.json();

        // Populate class dropdowns in both tabs
        const classFilter = document.getElementById("sidebar-class-filter");
        const classSelect = document.getElementById("sidebar-class-select");

        let classOpts = '<option value="">— Pilih kelas —</option>';
        sidebarStudentsList.forEach(c => {
            classOpts += `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`;
        });
        classFilter.innerHTML = classOpts;
        classSelect.innerHTML = classOpts;
    } catch { /* toast shown */ }
}

function switchSidebarTab(tab) {
    const tabs = ["student", "class", "rmt"];
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
    ["add", "remove"].forEach(m => {
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
    loadSettingsStudents();
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
