const PALETA = ["#0f766e", "#b45309", "#334155", "#7c3aed", "#be185d", "#0e7490",
                "#4d7c0f", "#a16207", "#475569", "#9f1239"];

function esc(str) {
    const el = document.createElement("span");
    el.textContent = str == null ? "" : String(str);
    return el.innerHTML;
}

const dados = Array.isArray(window.COMPARACAO) ? window.COMPARACAO : [];

const BASE_LAYOUT = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "'IBM Plex Mono', monospace", color: "#55504a", size: 11 },
    margin: { t: 14, r: 16, b: 40, l: 50 },
    xaxis: { gridcolor: "rgba(20,18,15,0.07)", zeroline: false, linecolor: "#e3ded4" },
    yaxis: { gridcolor: "rgba(20,18,15,0.07)", zeroline: false },
    hoverlabel: { bgcolor: "#ffffff", bordercolor: "#e3ded4",
                  font: { color: "#14120f", family: "'IBM Plex Mono', monospace" } },
};

function fmt(n, casas) {
    if (n == null) return "—";
    return casas == null ? new Intl.NumberFormat("pt-BR").format(n)
                         : new Intl.NumberFormat("pt-BR",
                             { maximumFractionDigits: casas }).format(n);
}

function renderVazio() {
    const vazio = !dados.length;
    document.getElementById("painel-vazio").hidden = !vazio;
    ["painel-tabela", "painel-score", "painel-curva"].forEach(id =>
        document.getElementById(id).hidden = vazio);
    if (!vazio) {
        const nomes = dados.map(d => d.repo.nome).join("  ×  ");
        document.getElementById("comp-alvo").textContent =
            `${nomes} · último período coletado`;
    }
    return vazio;
}

function renderTabela() {
    const linhas = [
        ["Commits (janela)", d => fmt(d.totais.commits)],
        ["Linhas +", d => fmt(d.totais.linhas_add)],
        ["Linhas −", d => fmt(d.totais.linhas_del)],
        ["Bus Factor", d => d.metricas && d.metricas.bus_factor != null ? d.metricas.bus_factor : "—"],
        ["TTFR (mediana, dias)", d => d.metricas ? fmt(d.metricas.ttfr, 2) : "—"],
        ["Churn relativo", d => d.metricas ? fmt(d.metricas.churn_relativo, 3) : "—"],
        ["Cadência (releases/mês)", d => d.metricas ? fmt(d.metricas.cadencia_releases, 2) : "—"],
        ["Issues abertas", d => d.metricas ? fmt(d.metricas.issues_abertas) : "—"],
        ["Issues fechadas", d => d.metricas ? fmt(d.metricas.issues_fechadas) : "—"],
        ["Score (0–100)", d => d.score && d.score.score != null ? fmt(d.score.score, 1) : "—"],
    ];
    const cabecalho = `<tr><th>Indicador</th>${dados.map(d =>
        `<th class="num">${esc(d.repo.nome)}</th>`).join("")}</tr>`;
    const corpo = linhas.map(([rotulo, val]) =>
        `<tr><td><strong>${esc(rotulo)}</strong></td>${dados.map(d =>
            `<td class="num">${val(d)}</td>`).join("")}</tr>`).join("");
    document.getElementById("tabela-comp").innerHTML =
        `<thead>${cabecalho}</thead><tbody>${corpo}</tbody>`;
}

function renderScore() {
    const comScore = dados.filter(d => d.score && d.score.score != null);
    if (!comScore.length) {
        document.getElementById("graf-score").innerHTML =
            '<p class="muted" style="padding:24px">Sem scores ainda — aguarde a coleta.</p>';
        return;
    }
    Plotly.newPlot("graf-score", [{
        x: comScore.map(d => d.repo.nome),
        y: comScore.map(d => d.score.score),
        type: "bar",
        marker: { color: comScore.map((d, i) => PALETA[i % PALETA.length]) },
        hovertemplate: "%{x}<br>score: %{y}<extra></extra>",
    }], {
        ...BASE_LAYOUT, height: 320,
        yaxis: { ...BASE_LAYOUT.yaxis, range: [0, 105] },
    }, { responsive: true, displayModeBar: false });
}

function renderCurvas() {
    const traces = dados
        .filter(d => d.curva && d.curva.length)
        .map((d, i) => ({
            x: d.curva.map(c => c.mes),
            y: d.curva.map(c => c.top5),
            type: "scatter", mode: "lines+markers",
            name: d.repo.nome,
            line: { color: PALETA[i % PALETA.length], width: 2.2 },
            hovertemplate: `${d.repo.nome}<br>%{x}<br>top-5: %{y}%<extra></extra>`,
        }));
    if (!traces.length) {
        document.getElementById("graf-curva-comp").innerHTML =
            '<p class="muted" style="padding:24px">Sem dados de autores mensais ainda.</p>';
        return;
    }
    Plotly.newPlot("graf-curva-comp", traces, {
        ...BASE_LAYOUT, height: 340,
        yaxis: { ...BASE_LAYOUT.yaxis, ticksuffix: "%", range: [0, 105] },
        showlegend: true,
        legend: { orientation: "h", x: 0, y: 1.14, font: { color: "#55504a" } },
    }, { responsive: true, displayModeBar: false });
}

if (!renderVazio()) {
    renderTabela();
    renderScore();
    renderCurvas();
}
