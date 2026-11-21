const $ = (seletor) => document.querySelector(seletor);
const cabecalho = $("#cabecalho");
const corpo = $("#corpo");
const vazio = $("#vazio");
const contagem = $("#contagem");
const campoBusca = $("#busca");
const botaoCadastrar = $("#btn-cadastrar");

const norm = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const real = (n) => n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const somenteDigitos = (s) => String(s ?? "").replace(/\D/g, "");

function dataBR(iso) {                  
  const [ano, mes, dia] = String(iso ?? "").split("-");
  return dia ? `${dia}/${mes}/${ano}` : (iso ?? "");
}

function mascaraDocumento(valor) {           
  const d = somenteDigitos(valor);
  if (d.length === 11) return d.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, "$1.$2.$3-$4");
  if (d.length === 14) return d.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, "$1.$2.$3/$4-$5");
  return valor ?? "";
}

function mascaraTelefone(valor) {
  const d = somenteDigitos(valor);
  if (d.length === 11) return d.replace(/(\d{2})(\d{5})(\d{4})/, "($1) $2-$3");
  if (d.length === 10) return d.replace(/(\d{2})(\d{4})(\d{4})/, "($1) $2-$3");
  return valor ?? "";
}

function esc(texto) {
  const d = document.createElement("div");
  d.textContent = texto;
  return d.innerHTML;
}

function mostrarAlerta(texto) {
  $("#alerta-texto").textContent = texto;
  $("#alerta").classList.add("show");
}
$("#alerta-fechar").addEventListener("click", () => $("#alerta").classList.remove("show"));

let temporizadorToast;
function mostrarToast(texto) {
  const toast = $("#toast");
  toast.textContent = texto;
  toast.classList.add("show");
  clearTimeout(temporizadorToast);
  temporizadorToast = setTimeout(() => toast.classList.remove("show"), 3000);
}

async function api(url, opcoes) {
  let resp;
  try {
    resp = await fetch(url, opcoes);
  } catch {
    throw new Error("Não foi possível falar com o servidor. O app.py está rodando?");
  }
  const dados = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(dados.erro || "Erro inesperado no servidor.");
  return dados;
}

const telas = {
  estoque: {
    titulo: "Movimentações de estoque",
    nomes: ["movimentação", "movimentações"],
    api: "/api/movimentacoes",
    dialogo: "janelaEstoque",
    botao: "+ Registrar movimentação",
    busca: "Buscar por produto, tipo ou observação…",
    vazio: "Nenhuma movimentação registrada ainda.",
    editavel: false,                    
    textoBusca: (m) => `${m.produto} ${m.tipo} ${m.observacao ?? ""}`,
    colunas: [
      { rotulo: "Data", valor: (m) => dataBR(m.data_movimentacao) },
      { rotulo: "Produto", valor: (m) => m.produto },
      { rotulo: "Tipo", valor: (m) => m.tipo, classe: (m) => (m.tipo === "Entrada" ? "entrada" : "saida") },
      { rotulo: "Quantidade", num: true,
        valor: (m) => `${m.tipo === "Entrada" ? "+" : "−"}${m.quantidade}`,
        classe: (m) => (m.tipo === "Entrada" ? "entrada" : "saida") },
      { rotulo: "Observação", valor: (m) => m.observacao ?? "" },
    ],
  },
  produtos: {
    titulo: "Produtos",
    nomes: ["produto", "produtos"],
    api: "/api/produtos",
    dialogo: "janelaProduto",
    botao: "+ Cadastrar produto",
    busca: "Buscar por produto, categoria ou fornecedor…",
    vazio: "Nenhum produto cadastrado ainda.",
    editavel: true,
    rotulo: (p) => p.nome,
    classeLinha: (p) => (p.estoque_atual < p.estoque_minimo ? "baixo" : ""),
    textoBusca: (p) => `${p.nome} ${p.categoria} ${p.fornecedor}`,
    colunas: [
      { rotulo: "Produto", valor: (p) => p.nome },
      { rotulo: "Categoria", valor: (p) => p.categoria },
      { rotulo: "Fornecedor", valor: (p) => p.fornecedor },
      { rotulo: "Preço", num: true, valor: (p) => real(p.preco) },
      { rotulo: "Estoque", num: true, valor: (p) => p.estoque_atual, classe: () => "est" },
      { rotulo: "Mínimo", num: true, valor: (p) => p.estoque_minimo },
    ],
  },
  categorias: {
    titulo: "Categorias",
    nomes: ["categoria", "categorias"],
    api: "/api/categorias",
    dialogo: "janelaCategoria",
    botao: "+ Cadastrar categoria",
    busca: "Buscar categoria…",
    vazio: "Nenhuma categoria cadastrada ainda.",
    editavel: true,
    rotulo: (c) => c.nome,
    textoBusca: (c) => c.nome,
    colunas: [
      { rotulo: "Categoria", valor: (c) => c.nome },
      { rotulo: "Produtos", num: true, valor: (c) => c.qtd_produtos },
    ],
  },
  fornecedores: {
    titulo: "Fornecedores",
    nomes: ["fornecedor", "fornecedores"],
    api: "/api/fornecedores",
    dialogo: "janelaFornecedor",
    botao: "+ Cadastrar fornecedor",
    busca: "Buscar por nome, CPF/CNPJ, telefone ou e-mail…",
    vazio: "Nenhum fornecedor cadastrado ainda.",
    editavel: true,
    rotulo: (f) => f.nome,
    textoBusca: (f) => `${f.nome} ${f.cpf_cnpj} ${f.telefone ?? ""} ${f.email ?? ""}`,
    colunas: [
      { rotulo: "Fornecedor", valor: (f) => f.nome },
      { rotulo: "CPF/CNPJ", valor: (f) => mascaraDocumento(f.cpf_cnpj) },
      { rotulo: "Telefone", valor: (f) => mascaraTelefone(f.telefone) },
      { rotulo: "E-mail", valor: (f) => f.email ?? "" },
      { rotulo: "Produtos", num: true, valor: (f) => f.qtd_produtos },
    ],
  },
};

let telaAtual = "produtos";
let itens = [];   
let carregado = false; 

// ---------- desenho da tabela ----------
function desenhar() {
  const tela = telas[telaAtual];
  const termo = norm(campoBusca.value.trim());
  const lista = itens.filter((i) => norm(tela.textoBusca(i)).includes(termo));

  cabecalho.innerHTML = `<tr>
    ${tela.colunas.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.rotulo)}</th>`).join("")}
    ${tela.editavel ? "<th></th>" : ""}
  </tr>`;

  corpo.innerHTML = lista.map((item) => `
    <tr class="${tela.classeLinha ? tela.classeLinha(item) : ""}">
      ${tela.colunas.map((c) => {
        const classes = [c.num ? "num" : "", c.classe ? c.classe(item) : ""].join(" ").trim();
        return `<td class="${classes}">${esc(String(c.valor(item) ?? ""))}</td>`;
      }).join("")}
      ${tela.editavel ? `<td><div class="acoes">
        <button type="button" class="btn-acao alterar" data-id="${item.id}">Alterar</button>
        <button type="button" class="btn-acao excluir" data-id="${item.id}">Excluir</button>
      </div></td>` : ""}
    </tr>`).join("");

  if (!carregado) {
    vazio.textContent = "Carregando…";
    vazio.hidden = false;
    contagem.textContent = "";
    return;
  }
  vazio.textContent = itens.length === 0 ? tela.vazio : "Nenhum resultado para a busca.";
  vazio.hidden = lista.length > 0;

  const plural = (n) => `${n} ${n === 1 ? tela.nomes[0] : tela.nomes[1]}`;
  contagem.textContent = lista.length === itens.length
    ? plural(itens.length)
    : `${lista.length} de ${plural(itens.length)}`;
}

async function carregar() {
  const nome = telaAtual;
  const lista = await api(telas[nome].api);
  if (nome !== telaAtual) return;   
  itens = lista;
  carregado = true;
  desenhar();
}

function trocarTela(nome) {
  if (!telas[nome]) nome = "produtos";
  telaAtual = nome;
  itens = [];
  carregado = false;
  campoBusca.value = "";
  $("#alerta").classList.remove("show");

  const tela = telas[nome];
  $("#titulo-tela").textContent = tela.titulo;
  botaoCadastrar.textContent = tela.botao;
  campoBusca.placeholder = tela.busca;
  $("#legenda").hidden = nome !== "produtos";
  document.querySelectorAll("nav [data-tela]").forEach((botao) => {
    const ativo = botao.dataset.tela === nome;
    botao.classList.toggle("ativo", ativo);
    if (ativo) botao.setAttribute("aria-current", "page");
    else botao.removeAttribute("aria-current");
  });

  desenhar();
  carregar().catch((err) => mostrarAlerta(err.message));
}

// ---------- menu e busca ----------
document.querySelectorAll("nav [data-tela]").forEach((botao) =>
  botao.addEventListener("click", () => {
    if (location.hash === `#${botao.dataset.tela}`) trocarTela(botao.dataset.tela);
    else location.hash = botao.dataset.tela;
  }));
window.addEventListener("hashchange", () => trocarTela(location.hash.slice(1)));

campoBusca.addEventListener("input", desenhar);

// ---------- botões da tabela: Alterar e Excluir ----------
corpo.addEventListener("click", async (e) => {
  const tela = telas[telaAtual];
  const botao = e.target.closest(".btn-acao");
  if (!botao) return;
  const id = Number(botao.dataset.id);
  const item = itens.find((i) => i.id === id);
  if (!item) return;

  if (botao.classList.contains("alterar")) {
    abrirDialogo(tela.dialogo, item);
    return;
  }

  if (!confirm(`Excluir ${tela.nomes[0]} "${tela.rotulo(item)}"?\nEssa ação não pode ser desfeita.`)) return;
  try {
    await api(`${tela.api}/${id}`, { method: "DELETE" });
    mostrarToast(`"${tela.rotulo(item)}" excluído.`);
  } catch (err) {
    mostrarAlerta(err.message);
  }
  carregar().catch(() => {});
});

// Botão grande no fim da tabela: abre a janela de cadastro da tela atual
botaoCadastrar.addEventListener("click", () => abrirDialogo(telas[telaAtual].dialogo));

// ---------- janelas (dialogs) ----------
function preencherSelect(select, lista, textoInicial, rotulo) {
  select.replaceChildren(new Option(textoInicial, ""));
  for (const item of lista) select.append(new Option(rotulo(item), item.id));
}

const preparos = {
  janelaProduto: async () => {
    const [categorias, fornecedores] = await Promise.all([
      api("/api/categorias"), api("/api/fornecedores"),
    ]);
    preencherSelect($("#prod-categoria"), categorias, "Escolha…", (c) => c.nome);
    preencherSelect($("#prod-fornecedor"), fornecedores, "Escolha…", (f) => f.nome);
    return categorias.length && fornecedores.length
      ? ""
      : "Cadastre ao menos uma categoria e um fornecedor antes de cadastrar produtos.";
  },
  janelaEstoque: async () => {
    const lista = await api("/api/produtos");
    preencherSelect($("#est-produto"), lista, "Escolha…",
      (p) => `${p.nome} (estoque: ${p.estoque_atual})`);
    return lista.length ? "" : "Cadastre um produto antes de movimentar o estoque.";
  },
};

// Coloca nos campos do formulário os valores do item que está sendo alterado
function preencherCampos(form, item) {
  for (const [campo, valor] of Object.entries(item)) {
    const el = form.elements[campo];
    if (el && "value" in el) el.value = valor ?? "";
  }
}

// Sem `item`: modo cadastro. Com `item`: modo alteração (mesma janela, campos preenchidos).
async function abrirDialogo(id, item = null) {
  const dialogo = document.getElementById(id);
  if (dialogo.open) return;
  const form = dialogo.querySelector("form");
  const erro = form.querySelector(".erro-form");
  const legenda = form.querySelector("legend");

  form.reset();
  erro.hidden = true;
  if (item) form.dataset.editando = item.id;
  else delete form.dataset.editando;
  if (legenda && legenda.dataset.novo) {
    legenda.textContent = item ? legenda.dataset.editar : legenda.dataset.novo;
  }

  try {
    const aviso = preparos[id] ? await preparos[id]() : "";
    if (item) preencherCampos(form, item); 
    if (aviso) { erro.textContent = aviso; erro.hidden = false; }
    if (!dialogo.open) dialogo.showModal();
  } catch (err) {
    mostrarAlerta(err.message);
  }
}

document.querySelectorAll("[data-fechar]").forEach((botao) =>
  botao.addEventListener("click", () => botao.closest("dialog").close()));

document.querySelectorAll("dialog form[data-url]").forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();             
    const erro = form.querySelector(".erro-form");
    const salvar = form.querySelector('button[type="submit"]');
    const editando = form.dataset.editando;
    erro.hidden = true;
    salvar.disabled = true;
    try {
      await api(editando ? `${form.dataset.url}/${editando}` : form.dataset.url, {
        method: editando ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      form.closest("dialog").close();
      mostrarToast(editando ? form.dataset.sucessoEdicao : form.dataset.sucesso);
      carregar().catch((err) => mostrarAlerta(err.message));
    } catch (err) {
      erro.textContent = err.message;
      erro.hidden = false;
    } finally {
      salvar.disabled = false;
    }
  });
});

// ---------- inicialização ----------
trocarTela(location.hash.slice(1));
setInterval(() => carregar().catch(() => {}), 10000);
