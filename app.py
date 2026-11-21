import re
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from flask import Flask, jsonify, request, send_from_directory
from psycopg import errors
from psycopg.rows import dict_row

from crud import cadastrar_categoria, cadastrar_fornecedor, cadastrar_produto
from database import conectar

BASE_DIR = Path(__file__).resolve().parent

# True: confere os dígitos verificadores do CPF/CNPJ. False: confere só o tamanho.
VALIDAR_DIGITOS_VERIFICADORES = True


def localizar_frontend():
    """Acha a pasta que contém o index.html (e a pasta src/)."""
    for pasta in (BASE_DIR, BASE_DIR / "frontend", BASE_DIR.parent / "frontend"):
        if (pasta / "index.html").is_file():
            return pasta
    return BASE_DIR


PASTA_FRONTEND = localizar_frontend()

# O index.html chama "src/css/style.css", então servimos a pasta src em /src
app = Flask(
    __name__,
    static_folder=str(PASTA_FRONTEND / "src"),
    static_url_path="/src",
)


# ---------------------------------------------------------------------------
# Infraestrutura: conexão, erros e leitura/validação dos dados recebidos
# ---------------------------------------------------------------------------
@contextmanager
def banco():
    """Abre uma conexão para a requisição e sempre a fecha no final.
    Se algo falhar antes do commit, o PostgreSQL desfaz tudo (rollback)."""
    conexao = conectar()
    try:
        yield conexao
    finally:
        conexao.close()


class ErroApi(Exception):
    """Erro 'esperado' (validação, duplicidade...) com mensagem para o usuário."""

    def __init__(self, mensagem, codigo=400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo


@app.errorhandler(ErroApi)
def tratar_erro_api(e):
    return jsonify({"erro": e.mensagem}), e.codigo


@app.errorhandler(psycopg.OperationalError)
def tratar_falha_conexao(e):
    app.logger.error("Falha de conexão com o banco: %s", e)
    return jsonify({
        "erro": "Não foi possível conectar ao banco de dados. "
                "Verifique se o PostgreSQL está ligado e se o arquivo .env está correto."
    }), 503


@app.errorhandler(psycopg.Error)
def tratar_erro_banco(e):
    app.logger.exception("Erro no banco de dados")
    return jsonify({"erro": "Erro inesperado no banco de dados."}), 500


def ler_json():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        raise ErroApi("Envie os dados em formato JSON.")
    return dados


def ler_texto(dados, campo, rotulo, maximo=100, obrigatorio=True, maiusculo=False):
    valor = dados.get(campo)
    texto = "" if valor is None else str(valor).strip()
    if obrigatorio and not texto:
        raise ErroApi(f'Preencha o campo "{rotulo}".')
    if len(texto) > maximo:
        raise ErroApi(f'O campo "{rotulo}" aceita até {maximo} caracteres.')
    return texto.upper() if maiusculo else texto


def ler_inteiro(dados, campo, rotulo, minimo=0):
    valor = dados.get(campo)
    if valor is None or isinstance(valor, bool) or str(valor).strip() == "":
        raise ErroApi(f'Preencha o campo "{rotulo}".')
    try:
        numero = int(str(valor).strip())
    except ValueError:
        raise ErroApi(f'O campo "{rotulo}" precisa ser um número inteiro.') from None
    if numero < minimo:
        raise ErroApi(f'O campo "{rotulo}" não pode ser menor que {minimo}.')
    return numero


def ler_preco(dados, campo, rotulo):
    valor = dados.get(campo)
    if valor is None or isinstance(valor, bool) or str(valor).strip() == "":
        raise ErroApi(f'Preencha o campo "{rotulo}".')
    try:
        numero = Decimal(str(valor).strip().replace(",", "."))
    except InvalidOperation:
        raise ErroApi(f'O campo "{rotulo}" precisa ser um número.') from None
    if not numero.is_finite() or numero < 0 or numero >= 100000000:
        raise ErroApi(f'O campo "{rotulo}" precisa estar entre 0 e 99.999.999,99.')
    return numero.quantize(Decimal("0.01"))


def somente_digitos(texto):
    return re.sub(r"\D", "", texto)


def cpf_valido(cpf):
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if (soma * 10) % 11 % 10 != int(cpf[tamanho]):
            return False
    return True


def cnpj_valido(cnpj):
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        if tamanho == 13:
            pesos = [6] + pesos
        resto = sum(int(cnpj[i]) * pesos[i] for i in range(tamanho)) % 11
        digito = 0 if resto < 2 else 11 - resto
        if digito != int(cnpj[tamanho]):
            return False
    return True


def executar_escrita(sql, parametros, nao_encontrado, conflitos):
    """Executa um UPDATE/DELETE de um único item.
    `conflitos` traduz erros de integridade do banco em mensagens amigáveis:
    {errors.UniqueViolation: ("mensagem", 409), ...}"""
    try:
        with banco() as conexao, conexao.cursor() as cursor:
            cursor.execute(sql, parametros)
            afetadas = cursor.rowcount
            conexao.commit()
    except psycopg.IntegrityError as e:
        for tipo, (mensagem, codigo) in conflitos.items():
            if isinstance(e, tipo):
                raise ErroApi(mensagem, codigo) from None
        raise
    if afetadas == 0:
        raise ErroApi(nao_encontrado, 404)
    return jsonify({"ok": True})


# Página
@app.get("/")
def index():
    return send_from_directory(PASTA_FRONTEND, "index.html")


# Produtos
def ler_produto(dados):
    return (
        ler_texto(dados, "nome", "Nome", maiusculo=True),
        ler_inteiro(dados, "categoria_id", "Categoria", minimo=1),
        ler_inteiro(dados, "fornecedor_id", "Fornecedor", minimo=1),
        ler_preco(dados, "preco", "Preço"),
        ler_inteiro(dados, "estoque_minimo", "Estoque mínimo"),
    )


def produto_duplicado(cursor, nome, ignorar_id=0):
    cursor.execute("SELECT 1 FROM produto WHERE nome = %s AND id <> %s", (nome, ignorar_id))
    return cursor.fetchone() is not None


@app.get("/api/produtos")
def listar_produtos():
    with banco() as conexao, conexao.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""
            SELECT p.id,
                   p.nome,
                   p.categoria_id,
                   c.nome AS categoria,
                   p.fornecedor_id,
                   f.nome AS fornecedor,
                   p.preco::float AS preco,
                   p.estoque_minimo,
                   p.estoque_atual,
                   p.data_de_cadastro::text AS data_de_cadastro
            FROM produto p
            JOIN categoria  c ON c.id = p.categoria_id
            JOIN fornecedor f ON f.id = p.fornecedor_id
            ORDER BY p.nome
        """)
        return jsonify(cursor.fetchall())


@app.post("/api/produtos")
def criar_produto():
    nome, categoria_id, fornecedor_id, preco, estoque_minimo = ler_produto(ler_json())
    try:
        with banco() as conexao:
            cursor = conexao.cursor()
            if produto_duplicado(cursor, nome):
                raise ErroApi("Já existe um produto com esse nome.", 409)
            cadastrar_produto(conexao, cursor, nome, categoria_id,
                              fornecedor_id, preco, estoque_minimo)
    except errors.ForeignKeyViolation:
        raise ErroApi("Categoria ou fornecedor não encontrado.") from None
    return jsonify({"ok": True}), 201


@app.put("/api/produtos/<int:produto_id>")
def alterar_produto(produto_id):
    nome, categoria_id, fornecedor_id, preco, estoque_minimo = ler_produto(ler_json())
    try:
        with banco() as conexao, conexao.cursor() as cursor:
            if produto_duplicado(cursor, nome, produto_id):
                raise ErroApi("Já existe um produto com esse nome.", 409)
            cursor.execute(
                """UPDATE produto
                   SET nome = %s, categoria_id = %s, fornecedor_id = %s,
                       preco = %s, estoque_minimo = %s
                   WHERE id = %s""",
                (nome, categoria_id, fornecedor_id, preco, estoque_minimo, produto_id),
            )
            afetadas = cursor.rowcount
            conexao.commit()
    except errors.ForeignKeyViolation:
        raise ErroApi("Categoria ou fornecedor não encontrado.") from None
    if afetadas == 0:
        raise ErroApi("Produto não encontrado.", 404)
    return jsonify({"ok": True})


@app.delete("/api/produtos/<int:produto_id>")
def excluir_produto(produto_id):
    return executar_escrita(
        "DELETE FROM produto WHERE id = %s", (produto_id,), "Produto não encontrado.",
        {errors.ForeignKeyViolation: (
            "Este produto possui movimentações de estoque registradas e não pode ser excluído.", 409)},
    )


# Categorias
@app.get("/api/categorias")
def listar_categorias():
    with banco() as conexao, conexao.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""
            SELECT c.id, c.nome, COUNT(p.id)::int AS qtd_produtos
            FROM categoria c
            LEFT JOIN produto p ON p.categoria_id = c.id
            GROUP BY c.id, c.nome
            ORDER BY c.nome
        """)
        return jsonify(cursor.fetchall())


@app.post("/api/categorias")
def criar_categoria():
    nome = ler_texto(ler_json(), "nome", "Nome", maiusculo=True)
    try:
        with banco() as conexao:
            cadastrar_categoria(conexao, conexao.cursor(), nome)
    except errors.UniqueViolation:
        raise ErroApi("Já existe uma categoria com esse nome.", 409) from None
    return jsonify({"ok": True}), 201


@app.put("/api/categorias/<int:categoria_id>")
def alterar_categoria(categoria_id):
    nome = ler_texto(ler_json(), "nome", "Nome", maiusculo=True)
    return executar_escrita(
        "UPDATE categoria SET nome = %s WHERE id = %s", (nome, categoria_id),
        "Categoria não encontrada.",
        {errors.UniqueViolation: ("Já existe uma categoria com esse nome.", 409)},
    )


@app.delete("/api/categorias/<int:categoria_id>")
def excluir_categoria(categoria_id):
    return executar_escrita(
        "DELETE FROM categoria WHERE id = %s", (categoria_id,), "Categoria não encontrada.",
        {errors.ForeignKeyViolation: (
            ("Esta categoria está cadastrada em produtos. "
             "Altere a categoria desses produtos para poder excluí-la."), 409)},
    )


# Fornecedores
def ler_fornecedor(dados):
    nome = ler_texto(dados, "nome", "Nome", maiusculo=True)

    # CPF/CNPJ: guardado só com números, como no terminal ("apenas números")
    documento = somente_digitos(ler_texto(dados, "cpf_cnpj", "CPF/CNPJ", maximo=18))
    if len(documento) == 11:
        if VALIDAR_DIGITOS_VERIFICADORES and not cpf_valido(documento):
            raise ErroApi("CPF inválido.")
    elif len(documento) == 14:
        if VALIDAR_DIGITOS_VERIFICADORES and not cnpj_valido(documento):
            raise ErroApi("CNPJ inválido.")
    else:
        raise ErroApi("Informe um CPF (11 dígitos) ou um CNPJ (14 dígitos).")

    telefone_digitado = ler_texto(dados, "telefone", "Telefone", maximo=20, obrigatorio=False)
    telefone = somente_digitos(telefone_digitado)
    if telefone_digitado and len(telefone) not in (10, 11):
        raise ErroApi("Informe o telefone com DDD (10 ou 11 dígitos).")

    email = ler_texto(dados, "email", "E-mail", obrigatorio=False, maiusculo=True)
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ErroApi("E-mail inválido.")

    return nome, documento, telefone or None, email or None


MSG_FORNECEDOR_DUPLICADO = "Já existe um fornecedor com esse nome ou esse CPF/CNPJ."


@app.get("/api/fornecedores")
def listar_fornecedores():
    with banco() as conexao, conexao.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""
            SELECT f.id, f.nome, f.cpf_cnpj, f.telefone, f.email,
                   COUNT(p.id)::int AS qtd_produtos
            FROM fornecedor f
            LEFT JOIN produto p ON p.fornecedor_id = f.id
            GROUP BY f.id, f.nome, f.cpf_cnpj, f.telefone, f.email
            ORDER BY f.nome
        """)
        return jsonify(cursor.fetchall())


@app.post("/api/fornecedores")
def criar_fornecedor():
    nome, cpf_cnpj, telefone, email = ler_fornecedor(ler_json())
    try:
        with banco() as conexao:
            cadastrar_fornecedor(conexao, conexao.cursor(), nome, cpf_cnpj, telefone, email)
    except errors.UniqueViolation:
        raise ErroApi(MSG_FORNECEDOR_DUPLICADO, 409) from None
    return jsonify({"ok": True}), 201


@app.put("/api/fornecedores/<int:fornecedor_id>")
def alterar_fornecedor(fornecedor_id):
    nome, cpf_cnpj, telefone, email = ler_fornecedor(ler_json())
    return executar_escrita(
        """UPDATE fornecedor
           SET nome = %s, cpf_cnpj = %s, telefone = %s, email = %s
           WHERE id = %s""",
        (nome, cpf_cnpj, telefone, email, fornecedor_id), "Fornecedor não encontrado.",
        {errors.UniqueViolation: (MSG_FORNECEDOR_DUPLICADO, 409)},
    )


@app.delete("/api/fornecedores/<int:fornecedor_id>")
def excluir_fornecedor(fornecedor_id):
    return executar_escrita(
        "DELETE FROM fornecedor WHERE id = %s", (fornecedor_id,), "Fornecedor não encontrado.",
        {errors.ForeignKeyViolation: (
            ("Este fornecedor está cadastrado em produtos. "
             "Altere o fornecedor desses produtos para poder excluí-lo."), 409)},
    )


# Estoque: histórico e registro de movimentações (Entrada / Saída)
@app.get("/api/movimentacoes")
def listar_movimentacoes():
    with banco() as conexao, conexao.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""
            SELECT e.id,
                   p.nome AS produto,
                   e.tipo,
                   e.quantidade,
                   e.data_movimentacao::text AS data_movimentacao,
                   e.observacao
            FROM estoque e
            JOIN produto p ON p.id = e.produto_id
            ORDER BY e.id DESC
            LIMIT 500
        """)
        return jsonify(cursor.fetchall())


@app.post("/api/movimentacoes")
def registrar_movimentacao():
    dados = ler_json()
    produto_id = ler_inteiro(dados, "produto_id", "Produto", minimo=1)
    tipo = ler_texto(dados, "tipo", "Tipo")
    quantidade = ler_inteiro(dados, "quantidade", "Quantidade", minimo=1)
    observacao = ler_texto(dados, "observacao", "Observação", maximo=200, obrigatorio=False) or None
    if tipo not in ("Entrada", "Saída"):
        raise ErroApi('O tipo deve ser "Entrada" ou "Saída".')

    with banco() as conexao, conexao.cursor() as cursor:
        # FOR UPDATE trava a linha do produto até o commit: duas saídas
        # simultâneas não conseguem passar do estoque disponível.
        cursor.execute("SELECT estoque_atual FROM produto WHERE id = %s FOR UPDATE", (produto_id,))
        linha = cursor.fetchone()
        if linha is None:
            raise ErroApi("Produto não encontrado.", 404)
        atual = linha[0]
        novo = atual + quantidade if tipo == "Entrada" else atual - quantidade
        if novo < 0:
            raise ErroApi(f"Estoque insuficiente: há {atual} unidade(s) disponível(is).", 409)

        cursor.execute(
            "INSERT INTO estoque (produto_id, tipo, quantidade, observacao) VALUES (%s, %s, %s, %s)",
            (produto_id, tipo, quantidade, observacao),
        )
        cursor.execute("UPDATE produto SET estoque_atual = %s WHERE id = %s", (novo, produto_id))
        conexao.commit()
    return jsonify({"ok": True, "estoque_atual": novo}), 201


if __name__ == "__main__":
    app.run(debug=True)
