// Contract Clause Analyzer - Frontend Application
let selectedFile = null;
let analysisResult = null;

// DOM Elements
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('file-info');
const analyzeBtn = document.getElementById('analyze-btn');
const loadingSection = document.getElementById('loading-section');
const outputSection = document.getElementById('output-section');
const errorSection = document.getElementById('error-section');
const progressFill = document.getElementById('progress-fill');
const loadingStatus = document.getElementById('loading-status');

// Drag & Drop Handlers
dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFileSelect(files[0]);
    }
});

dropZone.addEventListener('click', () => {
    fileInput.click();
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
    }
});

function handleFileSelect(file) {
    const validTypes = ['.pdf', '.docx', '.doc', '.txt', '.rtf'];
    const fileExt = '.' + file.name.split('.').pop().toLowerCase();
    
    if (!validTypes.includes(fileExt)) {
        alert('Invalid file type. Please upload PDF, DOCX, DOC, TXT, or RTF files.');
        return;
    }
    
    selectedFile = file;
    document.querySelector('.file-name').textContent = file.name;
    fileInfo.style.display = 'flex';
    dropZone.style.display = 'none';
    analyzeBtn.disabled = false;
}

function removeFile() {
    selectedFile = null;
    fileInput.value = '';
    fileInfo.style.display = 'none';
    dropZone.style.display = 'block';
    analyzeBtn.disabled = true;
}

// Analysis Handler
analyzeBtn.addEventListener('click', async () => {
    if (!selectedFile) return;
    
    const contextText = document.getElementById('context-text').value.trim();
    
    // Show loading state
    loadingSection.style.display = 'block';
    outputSection.style.display = 'none';
    errorSection.style.display = 'none';
    analyzeBtn.disabled = true;
    
    // Simulate progress updates
    let progress = 0;
    const progressInterval = setInterval(() => {
        progress += Math.random() * 15;
        if (progress > 90) progress = 90;
        progressFill.style.width = progress + '%';
        
        if (progress < 30) loadingStatus.textContent = 'Extracting text from document...';
        else if (progress < 60) loadingStatus.textContent = 'Analyzing clauses with AI...';
        else if (progress < 80) loadingStatus.textContent = 'Searching case law references...';
        else loadingStatus.textContent = 'Compiling final report...';
    }, 500);
    
    try {
        const formData = new FormData();
        formData.append('contract', selectedFile);
        formData.append('context', contextText);
        
        const response = await fetch('/api/analyze', {
            method: 'POST',
            body: formData
        });
        
        clearInterval(progressInterval);
        progressFill.style.width = '100%';
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Analysis failed');
        }
        
        analysisResult = await response.json();
        displayResults(analysisResult);
        
    } catch (error) {
        clearInterval(progressInterval);
        showError(error.message);
    } finally {
        loadingSection.style.display = 'none';
        analyzeBtn.disabled = false;
    }
});

function displayResults(result) {
    const clausesContainer = document.getElementById('clauses-container');
    clausesContainer.innerHTML = '';
    
    // Update stats
    const counts = { high: 0, medium: 0, low: 0 };
    result.clauses.forEach(clause => {
        counts[clause.risk_level.toLowerCase()]++;
    });
    
    document.getElementById('high-count').textContent = counts.high;
    document.getElementById('medium-count').textContent = counts.medium;
    document.getElementById('low-count').textContent = counts.low;
    
    // Render each clause
    result.clauses.forEach((clause, index) => {
        const card = document.createElement('div');
        card.className = `clause-card ${clause.risk_level.toLowerCase()}`;
        
        let referencesHtml = '';
        if (clause.legal_references && clause.legal_references.length > 0) {
            referencesHtml = `
                <div class="references">
                    <h4>📚 Legal References & Precedents</h4>
                    ${clause.legal_references.map(ref => `
                        <div class="reference-item">
                            <a href="${ref.url}" target="_blank" rel="noopener">${ref.title}</a>
                            ${ref.summary ? `<div class="reference-summary">${ref.summary.substring(0, 200)}${ref.summary.length > 200 ? '...' : ''}</div>` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
        }
        
        card.innerHTML = `
            <div class="clause-header">
                <span class="clause-type">${index + 1}. ${clause.clause_type}</span>
                <span class="risk-badge ${clause.risk_level.toLowerCase()}">${clause.risk_level} Risk</span>
            </div>
            <div class="clause-text">"${clause.clause_text}"</div>
            <div class="workaround">
                <h4>💡 Recommended Workaround</h4>
                <p>${clause.workaround}</p>
            </div>
            ${referencesHtml}
        `;
        
        clausesContainer.appendChild(card);
    });
    
    outputSection.style.display = 'block';
}

function showError(message) {
    document.getElementById('error-message').textContent = message;
    errorSection.style.display = 'block';
}

function resetForm() {
    selectedFile = null;
    analysisResult = null;
    fileInput.value = '';
    document.getElementById('context-text').value = '';
    fileInfo.style.display = 'none';
    dropZone.style.display = 'block';
    outputSection.style.display = 'none';
    errorSection.style.display = 'none';
    analyzeBtn.disabled = true;
    progressFill.style.width = '0%';
}

function exportReport(format) {
    if (!analysisResult) return;
    
    let content, filename, mimeType;
    
    if (format === 'json') {
        content = JSON.stringify(analysisResult, null, 2);
        filename = 'contract-analysis.json';
        mimeType = 'application/json';
    } else {
        content = generateMarkdown(analysisResult);
        filename = 'contract-analysis.md';
        mimeType = 'text/markdown';
    }
    
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function generateMarkdown(result) {
    let md = `# Contract Clause Analysis Report\n\n`;
    md += `**Generated:** ${new Date().toISOString()}\n\n`;
    md += `**Total Flagged Clauses:** ${result.clauses.length}\n\n---\n\n`;
    
    result.clauses.forEach((clause, i) => {
        md += `## ${i + 1}. ${clause.clause_type} [${clause.risk_level} RISK]\n\n`;
        md += `**Issue:** ${clause.issue_description}\n\n`;
        md += `### Clause Text\n> ${clause.clause_text}\n\n`;
        md += `### 💡 Recommended Workaround\n${clause.workaround}\n\n`;
        
        if (clause.legal_references && clause.legal_references.length > 0) {
            md += `### 📚 Legal References\n`;
            clause.legal_references.forEach(ref => {
                md += `- **[${ref.title}](${ref.url})**\n`;
                if (ref.summary) md += `  ${ref.summary}\n`;
            });
            md += '\n';
        }
        md += '---\n\n';
    });
    
    return md;
}
