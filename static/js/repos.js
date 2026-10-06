const repos = Array.isArray(window.REPOS_INICIAIS) ? window.REPOS_INICIAIS : [];
const selecionados = new Set();
let editandoId = null;

function esc(str) {
    const el = document.createElement("span");
    el.textContent = str == null ? "" : String(str);
    return el.innerHTML;
}

function atualizarBtnComparar() {
    document.getElementById("btn-comparar").hidden = selecionados.size < 2;
}

function fmtData(d) {
    if (!d) return "-";
    return new Date(d).toLocaleDateString("pt-BR");
}

function badgeStatus(repo) {
    if (repo.coletando) return '<span class="badge badge-amber">coletando…</span>';
    if (repo.coletado) return '<span class="badge badge-green">coletado · ' + fmtData(repo.atualizado_em) + "</span>";
    return '<span class="badge badge-gray">pendente</span>';
}

function badgeScore(v) {
    if (v == null) return '<span class="badge badge-gray">-</span>';
    const cor = v >= 70 ? "badge-green" : (v >= 40 ? "badge-amber" : "badge-red");
    return `<span class="badge ${cor}">${String(v).replace(".", ",")}</span>`;
}

function linhaEdicao(r) {
    return `
        <tr class="linha-edicao" data-id="${r.id_repositorio}">
            <td colspan="8">
                <form class="form-edicao" data-id="${r.id_repositorio}">
                    <input type="text" class="edit-nome" value="${esc(r.nome_exibicao || r.nome)}"
                           maxlength="150" placeholder="Nome de exibição" aria-label="Nome de exibição">
                    <input type="url" class="edit-url" value="${esc(r.url)}"
                           placeholder="https://github.com/owner/repo" aria-label="URL do repositório">
                    <button class="btn btn-primary btn-salvar" type="submit">salvar</button>
                    <button class="btn btn-ghost btn-cancelar" type="button">cancelar</button>
                </form>
            </td>
        </tr>`;
}

function render() {
    const body = document.getElementById("repo-body");
    if (!repos.length) {
        body.innerHTML = '<tr class="loading-row"><td colspan="8">Nenhum repositório - adicione o primeiro acima.</td></tr>';
        return;
    }
    body.innerHTML = repos.map(r => {
        if (String(r.id_repositorio) === editandoId) return linhaEdicao(r);
        return `
        <tr class="linha-repo" data-id="${r.id_repositorio}" style="cursor:pointer">
            <td class="col-sel"><input type="checkbox" class="sel-repo" data-id="${r.id_repositorio}"${selecionados.has(String(r.id_repositorio)) ? " checked" : ""}></td>
            <td><strong>${esc(r.nome_exibicao || r.nome)}</strong></td>
            <td class="url-col">${esc(r.url)}</td>
            <td class="num">${r.commits ?? "-"}</td>
            <td class="num">${r.autores ?? "-"}</td>
            <td class="num">${badgeScore(r.score)}</td>
            <td class="num">${badgeStatus(r)}</td>
            <td class="col-acoes">
                <button class="btn btn-ghost btn-editar" data-id="${r.id_repositorio}">editar</button>
            </td>
        </tr>`;
    }).join("");

    body.querySelectorAll("tr.linha-repo").forEach(tr => {
        tr.addEventListener("click", (e) => {
            if (e.target.closest(".btn-editar") || e.target.closest(".sel-repo")) return;
            window.location.href = "/repo/" + tr.dataset.id;
        });
    });
    body.querySelectorAll(".sel-repo").forEach(chk => {
        chk.addEventListener("change", () => {
            if (chk.checked) selecionados.add(chk.dataset.id);
            else selecionados.delete(chk.dataset.id);
            atualizarBtnComparar();
        });
    });
    body.querySelectorAll(".btn-editar").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            editandoId = btn.dataset.id;
            render();
        });
    });
    body.querySelectorAll(".form-edicao").forEach(form => {
        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            const btnSalvar = form.querySelector(".btn-salvar");
            btnSalvar.disabled = true;
            btnSalvar.textContent = "salvando…";
            try {
                const resp = await fetch("/repos/" + form.dataset.id, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        nome: form.querySelector(".edit-nome").value,
                        url: form.querySelector(".edit-url").value,
                    }),
                });
                const dados = await resp.json();
                if (!resp.ok) throw new Error(dados.erro || "Falha ao salvar.");
                editandoId = null;
                await recarregarRepos();
                await atualizarStatus();
            } catch (err) {
                alert(err.message);
                btnSalvar.disabled = false;
                btnSalvar.textContent = "salvar";
            }
        });
        form.querySelector(".btn-cancelar").addEventListener("click", () => {
            editandoId = null;
            render();
        });
    });
}

async function atualizarStatus() {
    try {
        const s = await fetch("/api/coleta/status").then(r => r.json());
        const badge = document.getElementById("coleta-badge");
        if (s.estado === "coletando") {
            badge.textContent = `coletando: ${s.repo_atual || "…"} (${s.repos_concluidos}/${s.total_repos || "?"})`;
            repos.forEach(r => r.coletando = r.nome === s.repo_atual);
            render();
        } else {
            badge.textContent = s.mensagem || (s.estado === "concluido" ? "coleta em dia" : s.estado);
            let mudou = false;
            repos.forEach(r => {
                if (r.coletando) { r.coletando = false; r.coletado = true; mudou = true; }
            });
            if (mudou) render();
        }
    } catch (e) { /* ignora falha de polling */ }
}

async function recarregarRepos() {
    try {
        const novos = await fetch("/api/repos").then(r => r.json());
        if (Array.isArray(novos)) {
            repos.splice(0, repos.length, ...novos);
            const idsVivos = new Set(repos.map(r => String(r.id_repositorio)));
            [...selecionados].forEach(id => { if (!idsVivos.has(id)) selecionados.delete(id); });
            atualizarBtnComparar();
            render();
        }
    } catch (e) { /* mantém a lista atual */ }
}

document.getElementById("btn-comparar").addEventListener("click", () => {
    if (selecionados.size < 2) return;
    window.location.href = "/comparar?ids=" + [...selecionados].join(",");
});

document.getElementById("form-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("btn-add");
    const erro = document.getElementById("form-erro");
    const ok = document.getElementById("form-ok");
    erro.hidden = true; ok.hidden = true;
    btn.disabled = true;
    btn.textContent = "Verificando…";
    try {
        const resp = await fetch("/repos", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                url: document.getElementById("campo-url").value,
                nome: document.getElementById("campo-nome").value,
            }),
        });
        const dados = await resp.json();
        if (!resp.ok) throw new Error(dados.erro || "Falha ao adicionar.");
        ok.textContent = "Repositório adicionado - coleta iniciada em segundo plano.";
        ok.hidden = false;
        document.getElementById("campo-url").value = "";
        document.getElementById("campo-nome").value = "";
        await recarregarRepos();
        await atualizarStatus();
    } catch (err) {
        erro.textContent = err.message;
        erro.hidden = false;
    } finally {
        btn.disabled = false;
        btn.textContent = "Adicionar";
    }
});

render();
atualizarStatus();
setInterval(atualizarStatus, 4000);
setInterval(recarregarRepos, 8000);
