// ZeroDep Doctor AI Dashboard Client Logic

let currentScanData = null;
let selectedDep = null;
let currentProposal = null;

document.addEventListener('DOMContentLoaded', () => {
  initHealthCheck();
  initEventListeners();
});

async function initHealthCheck() {
  const statusPill = document.getElementById('aiStatusPill');
  const modelText = document.getElementById('aiModelText');
  const dot = statusPill.querySelector('.pulse-dot');

  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    if (data.ai && data.ai.available && data.ai.model_ready) {
      dot.classList.add('ready');
      modelText.textContent = `Ollama: ${data.ai.configured_model} (Live)`;
    } else if (data.ai && data.ai.available) {
      dot.classList.add('ready');
      modelText.textContent = `Ollama Online (${data.ai.configured_model} fallback ready)`;
    } else {
      modelText.textContent = `AI Fallback: Deterministic Evidence Engine`;
    }
  } catch (e) {
    modelText.textContent = 'Backend Offline';
  }
}

function initEventListeners() {
  const analyzeBtn = document.getElementById('analyzeBtn');
  const fixtureSelect = document.getElementById('fixtureSelect');
  const pathInput = document.getElementById('projectPathInput');
  const tabBtns = document.querySelectorAll('.tab-btn');
  const loadDiffBtn = document.getElementById('loadDiffBtn');
  const runVerifyBtn = document.getElementById('runVerifyBtn');
  const reExplainBtn = document.getElementById('reExplainBtn');

  fixtureSelect.addEventListener('change', (e) => {
    pathInput.value = e.target.value;
  });

  analyzeBtn.addEventListener('click', () => {
    runProjectAudit(pathInput.value.trim());
  });

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const targetPane = document.getElementById(btn.dataset.tab);
      if (targetPane) targetPane.classList.add('active');
    });
  });

  const explicitApprovalCheckbox = document.getElementById('explicitApprovalCheckbox');
  const modelSelector = document.getElementById('modelSelector');

  if (explicitApprovalCheckbox) {
    explicitApprovalCheckbox.addEventListener('change', (e) => {
      runVerifyBtn.disabled = !e.target.checked;
      const approvalMsg = document.getElementById('verifyApprovalMsg');
      if (approvalMsg && e.target.checked) {
        approvalMsg.style.display = 'none';
      }
    });
  }

  if (modelSelector) {
    modelSelector.addEventListener('change', () => {
      if (selectedDep) queryAIExplanation(selectedDep);
    });
  }

  loadDiffBtn.addEventListener('click', () => {
    if (selectedDep) loadMigrationDiff(selectedDep);
  });

  runVerifyBtn.addEventListener('click', () => {
    if (selectedDep) {
      runSandboxedVerification(selectedDep);
    }
  });

  reExplainBtn.addEventListener('click', () => {
    if (selectedDep) queryAIExplanation(selectedDep);
  });

  // Auto-run first scan on demo project on load
  runProjectAudit('demo');
}

async function runProjectAudit(projectPath) {
  const analyzeBtn = document.getElementById('analyzeBtn');
  const spinner = document.getElementById('scanSpinner');
  const btnText = analyzeBtn.querySelector('.btn-text');

  spinner.classList.remove('hidden');
  btnText.textContent = 'Auditing AST...';
  analyzeBtn.disabled = true;

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: projectPath }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert(`Audit failed: ${err.detail || 'Unknown error'}`);
      return;
    }

    currentScanData = await res.json();
    renderScorecard(currentScanData);
    renderDependenciesTable(currentScanData.dependencies);

    // Auto-select first dependency
    if (currentScanData.dependencies && currentScanData.dependencies.length > 0) {
      selectDependency(currentScanData.dependencies[0]);
    }
  } catch (err) {
    alert(`Network error connecting to ZeroDep server: ${err.message}`);
  } finally {
    spinner.classList.add('hidden');
    btnText.textContent = 'Audit Codebase';
    analyzeBtn.disabled = false;
  }
}

function renderScorecard(data) {
  const s = data.score;
  document.getElementById('scoreCurrent').textContent = Math.round(s.current);
  document.getElementById('scorePotential').textContent = `${Math.round(s.potential)} / 100`;
  document.getElementById('scoreBar').style.width = `${Math.min(100, Math.max(0, s.current))}%`;

  const gradeBadge = document.getElementById('gradeBadge');
  gradeBadge.textContent = s.grade;
  gradeBadge.className = 'badge-grade';
  if (['A+', 'A', 'A-'].includes(s.grade)) gradeBadge.classList.add('grade-a');
  else if (['B+', 'B', 'B-'].includes(s.grade)) gradeBadge.classList.add('grade-b');
  else if (['C+', 'C', 'C-'].includes(s.grade)) gradeBadge.classList.add('grade-c');

  document.getElementById('scanDuration').textContent = `${data.duration}s AST scan`;

  document.getElementById('countUnused').textContent = s.breakdown.unused_count;
  document.getElementById('countReplaceable').textContent = s.breakdown.replaceable_count;
  document.getElementById('countEssential').textContent = s.breakdown.essential_count;

  document.getElementById('filesScanned').textContent = data.files_scanned || 0;
  document.getElementById('locScanned').textContent = data.loc || 0;

  const gm = data.graph_metrics || {};
  const nodeCount = typeof gm.nodes === 'number' ? gm.nodes : (Array.isArray(gm.nodes) ? gm.nodes.length : (gm.nodes || 0));
  const edgeCount = typeof gm.edges === 'number' ? gm.edges : (gm.edges || 0);
  document.getElementById('graphMetrics').textContent = `${nodeCount} / ${edgeCount}`;
  document.getElementById('graphCycles').textContent = gm.has_cycles ? 'Detected (!)' : 'None (DAG)';

  const auditStatusEl = document.getElementById('auditStatus');
  if (auditStatusEl && s.audit_status) {
    auditStatusEl.textContent = s.audit_status;
  }

  const manifestsRow = document.getElementById('manifestsRow');
  const manifestsEl = document.getElementById('manifestsFound');
  if (manifestsRow && manifestsEl) {
    if (data.manifest_files && data.manifest_files.length > 0) {
      manifestsRow.style.display = 'flex';
      manifestsEl.textContent = data.manifest_files.join(', ');
    } else {
      manifestsRow.style.display = 'flex';
      manifestsEl.textContent = 'None';
    }
  }

  const parseErrorsRow = document.getElementById('parseErrorsRow');
  const parseErrorsEl = document.getElementById('parseErrorsCount');
  if (parseErrorsRow && parseErrorsEl) {
    if (data.parse_errors && data.parse_errors.length > 0) {
      parseErrorsRow.style.display = 'flex';
      parseErrorsEl.textContent = `${data.parse_errors.length} file(s)`;
      parseErrorsEl.title = data.parse_errors.join('\n');
    } else {
      parseErrorsRow.style.display = 'none';
    }
  }
}

function renderDependenciesTable(deps) {
  const tbody = document.getElementById('depTableBody');
  tbody.innerHTML = '';

  if (!deps || deps.length === 0) {
    const undeclared = currentScanData && currentScanData.undeclared_third_party;
    if (undeclared && undeclared.length > 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-state" style="color:#f59e0b;">
        <strong>[!] Undeclared Third-Party Imports Detected:</strong> ${undeclared.join(', ')}<br>
        <span style="font-size:0.85em; color:var(--text-dim);">No manifest file found declaring these packages. Add requirements.txt or pyproject.toml to ensure reproducible builds.</span>
      </td></tr>`;
    } else {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No declared dependencies detected. Codebase is genuine zero-dependency.</td></tr>';
    }
    return;
  }

  deps.forEach(dep => {
    const tr = document.createElement('tr');
    tr.dataset.pkg = dep.package;

    let badgeClass = 'badge-essential';
    if (dep.status === 'UNUSED' || dep.status === 'IMPORTED_BUT_UNUSED') badgeClass = 'badge-unused';
    else if (dep.status === 'REPLACEABLE') badgeClass = 'badge-replaceable';

    tr.innerHTML = `
      <td><strong>${dep.package}</strong></td>
      <td><span class="badge ${badgeClass}">${dep.status}</span></td>
      <td>${dep.confidence}</td>
      <td>${dep.risk_score} <span class="text-dim">(${dep.risk_level})</span></td>
      <td>${dep.impact_level}</td>
      <td>${dep.occurrences}</td>
      <td><button class="btn btn-outline btn-xs inspect-btn">Inspect</button></td>
    `;

    tr.addEventListener('click', () => {
      document.querySelectorAll('#depTableBody tr').forEach(r => r.classList.remove('selected'));
      tr.classList.add('selected');
      selectDependency(dep);
    });

    tbody.appendChild(tr);
  });
}

function selectDependency(dep) {
  selectedDep = dep;

  // Highlight row
  document.querySelectorAll('#depTableBody tr').forEach(r => {
    if (r.dataset.pkg === dep.package) r.classList.add('selected');
    else r.classList.remove('selected');
  });

  // Update header
  document.getElementById('depTitle').textContent = dep.package;
  document.getElementById('depSubtitle').textContent = `${dep.status} • Risk: ${dep.risk_score} (${dep.risk_level}) • Impact: ${dep.impact_level}`;

  const catBadge = document.getElementById('depCategoryBadge');
  catBadge.textContent = dep.status;
  catBadge.className = 'badge';
  if (dep.status === 'UNUSED' || dep.status === 'IMPORTED_BUT_UNUSED') catBadge.classList.add('badge-unused');
  else if (dep.status === 'REPLACEABLE') catBadge.classList.add('badge-replaceable');
  else catBadge.classList.add('badge-essential');

  document.getElementById('depConfidenceBadge').textContent = `Confidence: ${dep.confidence}`;

  // Tab 1: AST Evidence
  const evList = document.getElementById('evidenceList');
  evList.innerHTML = '';
  if (dep.evidence && dep.evidence.length > 0) {
    dep.evidence.forEach(e => {
      const li = document.createElement('li');
      li.textContent = e;
      evList.appendChild(li);
    });
  } else {
    evList.innerHTML = '<li class="empty-text">No static evidence lines found.</li>';
  }

  // Symbol tags
  const symTags = document.getElementById('symbolTags');
  symTags.innerHTML = '';
  const symKeys = Object.keys(dep.symbols || {});
  if (symKeys.length > 0) {
    symKeys.forEach(s => {
      const span = document.createElement('span');
      span.className = 'symbol-tag';
      span.textContent = `${s} (${dep.symbols[s]}x)`;
      symTags.appendChild(span);
    });
  } else {
    symTags.innerHTML = '<span class="empty-text">0 AST call symbols detected.</span>';
  }

  // Files
  const fList = document.getElementById('filesList');
  fList.innerHTML = '';
  if (dep.files && dep.files.length > 0) {
    dep.files.forEach(f => {
      const div = document.createElement('div');
      div.className = 'file-item';
      div.textContent = `📄 ${f}`;
      fList.appendChild(div);
    });
  } else {
    fList.innerHTML = '<span class="empty-text">Manifest only (not referenced in source files).</span>';
  }

  // Auto-query AI reasoning and migration diff
  queryAIExplanation(dep);
  loadMigrationDiff(dep);

  // Reset verification pane and approval checkbox
  const approvalBox = document.getElementById('explicitApprovalCheckbox');
  if (approvalBox) {
    approvalBox.checked = false;
    document.getElementById('runVerifyBtn').disabled = true;
  }
  const approvalMsg = document.getElementById('verifyApprovalMsg');
  if (approvalMsg) approvalMsg.style.display = 'none';
  const diffNotice = document.getElementById('verifyDiffNotice');
  if (diffNotice) diffNotice.style.display = 'none';

  document.getElementById('verifyStatusBadge').textContent = 'NOT RUN';
  document.getElementById('verifyStatusBadge').className = 'badge';
  document.getElementById('verifyExitCode').textContent = '--';
  document.getElementById('verifyCommand').textContent = '--';
  document.getElementById('verifyDuration').textContent = '--';
  document.getElementById('verifyOutput').textContent = 'Run verification to inspect subprocess test execution output.';
}

async function queryAIExplanation(dep) {
  const content = document.getElementById('aiContent');
  const tag = document.getElementById('aiEngineTag');
  const modelSelector = document.getElementById('modelSelector');
  const chosenModel = modelSelector ? modelSelector.value : 'llama3.2';

  content.innerHTML = `<div class="empty-state"><span class="spinner"></span> Grounding ${chosenModel} reasoning on AST evidence...</div>`;

  try {
    const res = await fetch('/api/explain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_path: currentScanData.target_dir,
        package: dep.package,
        model: chosenModel,
      }),
    });

    if (!res.ok) throw new Error('Failed to retrieve explanation');

    const exp = await res.json();
    tag.textContent = `${exp.provider.toUpperCase()} [${exp.model}]`;

    let html = `
      <div class="ai-section-title">Purpose & Code Usage</div>
      <p class="ai-p">${exp.why_used}</p>

      <div class="ai-section-title">Migration Benefits</div>
      <ul class="ai-ul">
        ${exp.benefits.map(b => `<li>${b}</li>`).join('')}
      </ul>

      <div class="ai-section-title">Trade-offs & Considerations</div>
      <ul class="ai-ul">
        ${exp.trade_offs.map(t => `<li>${t}</li>`).join('')}
      </ul>

      <div class="ai-section-title">Technical Risks</div>
      <ul class="ai-ul">
        ${exp.risks.map(r => `<li>${r}</li>`).join('')}
      </ul>

      <div class="ai-section-title">Verification Checklist</div>
      <ul class="ai-ul">
        ${exp.verification_checklist.map(v => `<li>${v}</li>`).join('')}
      </ul>
    `;

    if (exp.error) {
      html += `<div style="margin-top:0.75rem; font-size:0.75rem; color:#f87171;">Notice: ${exp.error}</div>`;
    }

    content.innerHTML = html;
  } catch (err) {
    content.innerHTML = `<div class="empty-state" style="color:#f87171;">Failed to fetch explanation: ${err.message}</div>`;
  }
}

async function loadMigrationDiff(dep) {
  const viewer = document.getElementById('diffViewer');
  const summaryText = document.getElementById('diffSummaryText');
  viewer.textContent = 'Generating reviewable diff...';

  try {
    const res = await fetch('/api/proposal/diff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_path: currentScanData.target_dir,
        package: dep.package,
      }),
    });

    if (!res.ok) throw new Error('Failed to generate proposal diff');

    currentProposal = await res.json();
    summaryText.textContent = currentProposal.summary;

    if (currentProposal.file_diffs && currentProposal.file_diffs.length > 0) {
      const fullDiff = currentProposal.file_diffs
        .map(d => `# File: ${d.file_path} (${d.reason})\n${d.diff_patch || '# No code changes detected in this file.'}`)
        .join('\n\n');
      viewer.textContent = fullDiff;
    } else if (currentProposal.recipe_before && currentProposal.recipe_after) {
      viewer.textContent = `# Stdlib Migration Recipe for ${dep.package}:\n\n--- BEFORE ---\n${currentProposal.recipe_before}\n\n+++ AFTER (Stdlib Drop-in) +++\n${currentProposal.recipe_after}\n\n# Limitations:\n# ${currentProposal.limitations}`;
    } else {
      viewer.textContent = `# No automatic diff available for ${dep.package}.\n# Reason: ${currentProposal.limitations}`;
    }
  } catch (err) {
    viewer.textContent = `Error loading diff: ${err.message}`;
  }
}

async function runSandboxedVerification(dep) {
  const runBtn = document.getElementById('runVerifyBtn');
  const statusBadge = document.getElementById('verifyStatusBadge');
  const exitCode = document.getElementById('verifyExitCode');
  const cmdElem = document.getElementById('verifyCommand');
  const durationElem = document.getElementById('verifyDuration');
  const outputElem = document.getElementById('verifyOutput');
  const approvalBox = document.getElementById('explicitApprovalCheckbox');
  const approvalMsg = document.getElementById('verifyApprovalMsg');
  const diffNotice = document.getElementById('verifyDiffNotice');

  if (approvalMsg) approvalMsg.style.display = 'none';

  if (!approvalBox || !approvalBox.checked) {
    if (approvalMsg) approvalMsg.style.display = 'block';
    statusBadge.textContent = 'APPROVAL REQUIRED';
    statusBadge.className = 'badge badge-unused';
    outputElem.textContent = 'Verification blocked: Developer must review the migration proposal and check the Explicit Approval box before executing sandboxed tests.';
    return;
  }

  // Prevent accidental duplicate requests while already running
  if (runBtn.disabled && runBtn.textContent.includes('Verifying')) {
    return;
  }

  runBtn.disabled = true;
  runBtn.textContent = 'Verifying in Sandbox...';
  statusBadge.textContent = 'RUNNING';
  statusBadge.className = 'badge badge-replaceable';
  outputElem.textContent = 'Setting up isolated temporary workspace and executing verification subprocess...';

  try {
    // If current proposal has not loaded yet or belongs to a different package, load it first
    if (!currentProposal || currentProposal.package !== dep.package) {
      await loadMigrationDiff(dep);
    }

    // Construct file patches from proposal
    const filePatches = {};
    let patchCount = 0;
    if (currentProposal && currentProposal.file_diffs) {
      currentProposal.file_diffs.forEach(fd => {
        if (fd.can_apply && fd.proposed_content) {
          filePatches[fd.file_path] = fd.proposed_content;
          patchCount++;
        }
      });
    }

    if (diffNotice) {
      diffNotice.style.display = patchCount === 0 ? 'block' : 'none';
    }

    const res = await fetch('/api/proposal/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_path: currentScanData.target_dir,
        package: dep.package,
        file_patches: filePatches,
      }),
    });

    if (!res.ok) {
      let errDetail = 'Verification request failed';
      try {
        const errJson = await res.json();
        errDetail = errJson.detail || errDetail;
      } catch (_) {}
      throw new Error(`HTTP ${res.status}: ${errDetail}`);
    }

    const result = await res.json();
    statusBadge.textContent = result.status;
    statusBadge.className = 'badge';
    if (result.status === 'PASS') {
      statusBadge.classList.add('badge-grade', 'grade-a');
    } else if (result.status === 'TIMEOUT') {
      statusBadge.classList.add('badge-unused');
      statusBadge.style.backgroundColor = '#ea580c';
    } else {
      statusBadge.classList.add('badge-unused');
    }

    exitCode.textContent = result.exit_code;
    cmdElem.textContent = result.command || 'compileall / unittest';
    durationElem.textContent = `${result.execution_time_seconds}s`;

    let out = '';
    if (result.summary) {
      out += `=== Summary: ${result.summary} ===\n\n`;
    }
    if (patchCount > 0) {
      out += `[Sandbox] Applied ${patchCount} migration patch file(s).\n`;
    } else {
      out += `[Sandbox] Baseline verification (0 patch files applied).\n`;
    }
    if (result.stdout) {
      out += `--- Subprocess STDOUT ---\n${result.stdout}\n`;
    }
    if (result.stderr) {
      out += `--- Subprocess STDERR ---\n${result.stderr}\n`;
    }
    if (!result.stdout && !result.stderr) {
      out += `[Subprocess completed with exit code ${result.exit_code} with empty stdout/stderr.]\n`;
    }
    outputElem.textContent = out.trim();
  } catch (err) {
    statusBadge.textContent = 'ERROR';
    statusBadge.className = 'badge badge-unused';
    outputElem.textContent = `Sandbox Error: ${err.message}\nCheck server logs or verify target project path.`;
  } finally {
    runBtn.disabled = !approvalBox.checked;
    runBtn.textContent = 'Run Isolated Verification';
  }
}
