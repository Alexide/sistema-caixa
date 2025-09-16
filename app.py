import os
from datetime import datetime, timedelta
import locale

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, flash
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from functools import wraps
import json
import secrets



# =========================
# Config Inicial
# =========================
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma-chave-secreta-muito-dificil-de-adivinhar'

# --- ALTERAÇÃO PARA BANCO DE DADOS ONLINE ---
# Procura por uma variável de ambiente DATABASE_URL (que o Render irá fornecer)
db_url = os.environ.get('DATABASE_URL')
if db_url:
    # Se estiver online, usa a URL do PostgreSQL do Render
    # A pequena correção .replace() garante a compatibilidade
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url.replace("://", "ql://", 1)
else:
    # Se estiver no seu computador, continua a usar o SQLite local
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'dados.sqlite')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Filtro Jinja: currency_br (R$ 1.234,56) ---
def currency_br(value):
    try:
        val = float(value or 0)
    except (TypeError, ValueError):
        val = 0.0
    sign = '-' if val < 0 else ''
    val = abs(val)
    # 1.234,56
    s = f"{val:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}R$ {s}"

app.jinja_env.filters['currency_br'] = currency_br


# Denominações para a abertura (totais por nota/moeda)
DENOMS = [0.05, 0.10, 0.25, 0.50, 1, 2, 5, 10, 20, 50, 100]

def require_admin(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Acesso restrito a administradores.', 'warning')
            return redirect(url_for('homepage'))
        return view_func(*args, **kwargs)
    return wrapper


def parse_brl_to_float(s: str) -> float:
    """
    Converte 'R$ 1.234,56' / '1.234,56' / '1234,56' -> 1234.56
    e trata None/vazio sem quebrar.
    """
    if not s:
        return 0.0
    s = str(s).strip()
    # mantém apenas dígitos, vírgula, ponto e sinal
    s = "".join(ch for ch in s if ch.isdigit() or ch in ",.-")
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0
    
from datetime import datetime, timedelta

def parse_ddmmyyyy(s: str) -> datetime:
    """Aceita 'dd/mm/aaaa' ou 'aaaa-mm-dd' e devolve datetime."""
    s = (s or '').strip()
    if not s:
        return None
    if '-' in s:  # input date
        y, m, d = s.split('-')
        return datetime(int(y), int(m), int(d))
    d, m, y = s.split('/')
    return datetime(int(y), int(m), int(d))

def fmt_ddmmyyyy(dt: datetime) -> str:
    return dt.strftime('%d/%m/%Y')

    
def coerce_to_br_date(raw: str) -> str:
    """
    Converte 'YYYY-MM-DD' (input type=date) ou 'DD/MM/YYYY' para 'DD/MM/YYYY'.
    Se vier vazio/ruim, devolve a data de hoje.
    """
    raw = (raw or '').strip()
    if not raw:
        return datetime.now().strftime('%d/%m/%Y')
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime('%d/%m/%Y')
        except ValueError:
            pass
    return datetime.now().strftime('%d/%m/%Y')


# =========================
# Flask-Login
# =========================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Por favor, inicie a sessão para aceder a esta página.'
login_manager.login_message_category = 'info'

# =========================
# Models
# =========================
class LivroFinanceiro(db.Model):
    """
    Lançamentos gerais fora do fechamento de caixa.
    - tipo: 'compra' ou 'saida'
    - grupo: categoria (Mercado, Bebidas, Gás, Entregador, Contas, etc)
    - forma_pagamento: Dinheiro, Débito, Crédito, PIX, Inter, Itaú, Valori, InfinitePay, Mercado Pago...
    """
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.String(10), nullable=False)     # dd/mm/aaaa
    tipo = db.Column(db.String(12), nullable=False)     # compra | saida
    grupo = db.Column(db.String(80), nullable=False)    # ex: MERCADO, BEBIDAS, GÁS...
    descricao = db.Column(db.String(255), default='')
    valor = db.Column(db.Float, default=0)
    forma_pagamento = db.Column(db.String(40), default='Dinheiro')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='user')
    registros = db.relationship('RegistroDiario', backref='author', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Sangria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    forma_pagamento = db.Column(db.String(50))
    registro_id = db.Column(db.Integer, db.ForeignKey('registro_diario.id'), nullable=False)

class RegistroDiario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='aberto')
    sangrias = db.relationship('Sangria', backref='registro', lazy=True, cascade="all, delete-orphan")

    valor_inicial_caixa = db.Column(db.Float, default=0)
    valor_final_caixa = db.Column(db.Float, default=0)
    ifood_vendas = db.Column(db.Float, default=0)
    ifood_pedidos = db.Column(db.Integer, default=0)
    ifood_cancelamento = db.Column(db.Float, default=0)
    food99_vendas = db.Column(db.Float, default=0)
    food99_pedidos = db.Column(db.Integer, default=0)
    pedidos_balcao = db.Column(db.Integer, default=0)
    pedidos_zap = db.Column(db.Integer, default=0)
    pedidos_vuca = db.Column(db.Integer, default=0)
    taxa_entrega = db.Column(db.Float, default=0)

    mp_debito = db.Column(db.Float, default=0)
    mp_credito = db.Column(db.Float, default=0)
    mp_pix = db.Column(db.Float, default=0)
    itau1_debito = db.Column(db.Float, default=0)
    itau1_credito = db.Column(db.Float, default=0)
    itau1_pix = db.Column(db.Float, default=0)
    itau2_debito = db.Column(db.Float, default=0)
    itau2_credito = db.Column(db.Float, default=0)
    itau2_pix = db.Column(db.Float, default=0)
    itau3_debito = db.Column(db.Float, default=0)
    itau3_credito = db.Column(db.Float, default=0)
    itau3_pix = db.Column(db.Float, default=0)
    valori_debito = db.Column(db.Float, default=0)
    valori_credito = db.Column(db.Float, default=0)
    valori_pix = db.Column(db.Float, default=0)
    infinitepay_debito = db.Column(db.Float, default=0)
    infinitepay_credito = db.Column(db.Float, default=0)
    infinitepay_pix = db.Column(db.Float, default=0)
    c6_pix = db.Column(db.Float, default=0)

    vuca_delivery_dinheiro = db.Column(db.Float, default=0)
    vuca_delivery_debito = db.Column(db.Float, default=0)
    vuca_delivery_credito = db.Column(db.Float, default=0)
    vuca_delivery_pix = db.Column(db.Float, default=0)
    vuca_balcao_dinheiro = db.Column(db.Float, default=0)
    vuca_balcao_debito = db.Column(db.Float, default=0)
    vuca_balcao_credito = db.Column(db.Float, default=0)
    vuca_balcao_pix = db.Column(db.Float, default=0)

class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # dd/mm/aaaa
    data = db.Column(db.String(10), nullable=False)

    # "compra" ou "saida"
    tipo = db.Column(db.String(10), nullable=False)

    # ex.: "MERCADO", "BEBIDAS", "COMBUSTIVEL", etc.
    grupo = db.Column(db.String(60), nullable=False)

    descricao = db.Column(db.String(200), default="")
    valor = db.Column(db.Float, default=0.0)

    # opcional: "Dinheiro", "MP", "Itaú", "PIX", etc.
    forma_pagamento = db.Column(db.String(30), default="")

    # opcional (quem lançou)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


# --- Nomes bonitos para exibição no relatório admin
ACQ_DISPLAY = {
    "mp": "Mercado Pago",
    "itau": "Itaú",         # agregado de itau1/itau2/itau3
    "valori": "Valori",
    "infinitepay": "InfinitePay",
    "c6": "C6 (PIX CNPJ)",
}

# --- Percentuais de taxa por adquirente e meio
# Você pode ajustar quando quiser. Os valores abaixo seguem sua planilha.
FEE_PERCENTS = {
    ("Mercado Pago",  "debito"):  0.0199,
    ("Mercado Pago",  "credito"): 0.0498,
    ("Mercado Pago",  "pix"):     0.0049,   # se seu PIX MP for 0%, troque para 0.0

    ("Itaú",          "debito"):  0.0097,
    ("Itaú",          "credito"): 0.0270,
    ("Itaú",          "pix"):     0.0000,

    ("Valori",        "debito"):  0.0097,
    ("Valori",        "credito"): 0.0270,
    ("Valori",        "pix"):     0.0000,

    ("InfinitePay",   "debito"):  0.0144,
    ("InfinitePay",   "credito"): 0.0289,
    ("InfinitePay",   "pix"):     0.0000,

    ("C6 (PIX CNPJ)", "pix"):     0.0000,
}


# =========================
# Rotas de Autenticação
# =========================

# =========================
# Rota de Inicialização (NOVO!)
# =========================
@app.route('/init-db')
def init_db_command():
    """
    Cria as tabelas do banco de dados e o primeiro usuário admin.
    Esta rota deve ser acedida apenas uma vez.
    """
    try:
        # Verifica se a tabela de usuários já existe para evitar recriar tudo
        if db.engine.has_table('user'):
             # Se já existe, apenas verifica se o admin existe
            admin_user = User.query.filter_by(username='admin').first()
            if admin_user:
                return "Banco de dados já inicializado e admin já existe."
        
        # Cria todas as tabelas
        db.create_all()

        # Cria o usuário admin se ele não existir
        admin_user = User.query.filter_by(username='admin').first()
        if not admin_user:
            admin = User(username='admin', role='admin')
            # Use uma senha padrão ou uma vinda de variáveis de ambiente
            admin.set_password('admin123') 
            db.session.add(admin)
            db.session.commit()
            return "Banco de dados inicializado e usuário 'admin' criado com a senha 'admin123'!"
        
        return "Banco de dados inicializado com sucesso (admin já existia)."

    except Exception as e:
        return f"Ocorreu um erro: {e}"


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user)
            return redirect(url_for('homepage'))
        flash('Nome de utilizador ou password inválidos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sessão terminada com sucesso.', 'success')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    # Bloqueia para não-admin
    if current_user.role != 'admin':
        flash('Cadastro só pode ser feito por administradores.', 'warning')
        return redirect(url_for('homepage'))
    return redirect(url_for('admin_users'))  # manda para a tela de admin

@app.route('/admin/users', methods=['GET', 'POST'])
@require_admin
def admin_users():
    """
    GET: lista usuários e mostra formulário de criação
    POST: cria usuário novo (username único, senha definida pelo admin)
    """
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        role     = (request.form.get('role') or 'user').strip()

        if not username or not password:
            flash('Preencha usuário e senha.', 'danger')
            return redirect(url_for('admin_users'))

        if User.query.filter_by(username=username).first():
            flash('Já existe um usuário com esse nome.', 'warning')
            return redirect(url_for('admin_users'))

        if role not in ('admin', 'user', 'caixa'):
            role = 'user'

        novo = User(username=username, role=role)
        novo.set_password(password)
        db.session.add(novo)
        db.session.commit()

        flash('Usuário criado com sucesso.', 'success')
        return redirect(url_for('admin_users'))

    # GET
    usuarios = User.query.order_by(User.role.desc(), User.username.asc()).all()
    return render_template('admin_users.html', usuarios=usuarios)


@app.route('/admin/users/<int:user_id>/reset', methods=['POST'])
@require_admin
def admin_users_reset(user_id):
    """
    Redefine a senha do usuário. Se nenhum password vier, gera uma senha aleatória curta.
    """
    user = User.query.get_or_404(user_id)

    new_password = (request.form.get('password') or '').strip()
    generated = False
    if not new_password:
        # 10 chars pseudo-aleatória para casos rápidos
        new_password = secrets.token_urlsafe(8)
        generated = True

    user.set_password(new_password)
    db.session.commit()

    if generated:
        flash(f'Senha redefinida. Nova senha temporária: {new_password}', 'info')
    else:
        flash('Senha redefinida com sucesso.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@require_admin
def admin_users_delete(user_id):
    """
    Remove usuário com salvaguardas:
    - Admin não pode deletar a si mesmo
    - Não pode apagar o último admin do sistema
    """
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('Você não pode remover a si mesmo.', 'warning')
        return redirect(url_for('admin_users'))

    # se for admin, verifica se há outros admins
    if user.role == 'admin':
        admins = User.query.filter_by(role='admin').count()
        if admins <= 1:
            flash('Não é possível remover o único administrador.', 'danger')
            return redirect(url_for('admin_users'))

    # Opcional: impedir remoção se o usuário tem registros (trocar ownership ou bloquear)
    # Exemplo simples: permitir exclusão mesmo assim.
    db.session.delete(user)
    db.session.commit()
    flash('Usuário removido.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/lancamentos', methods=['GET', 'POST'])
@require_admin
def admin_lancamentos():
    # Período padrão = semana atual (seg → dom)
    today = datetime.now()
    start_default = today - timedelta(days=today.weekday())  # segunda
    end_default   = start_default + timedelta(days=6)        # domingo

    # ---------- POST = criar/editar ----------
    if request.method == 'POST':
        edit_id     = (request.form.get('edit_id') or '').strip()     # hidden opcional p/ edição
        data_raw    = (request.form.get('data') or '').strip()        # yyyy-mm-dd do <input type=date>
        data_fmt    = fmt_ddmmyyyy(parse_ddmmyyyy(data_raw))          # salva como dd/mm/aaaa
        tipo_compra = (request.form.get('grupo') or request.form.get('tipo_compra') or '').strip()
        descricao   = (request.form.get('descricao') or '').strip()
        valor       = parse_brl_to_float(request.form.get('valor'))
        forma_pgto  = (request.form.get('forma_pagamento') or '').strip()

        if edit_id:
            item = LivroFinanceiro.query.get_or_404(int(edit_id))
            if item.user_id != current_user.id:
                flash('Sem permissão para editar este lançamento.', 'danger')
                return redirect(url_for('admin_lancamentos'))

            item.data            = data_fmt
            item.tipo            = 'compra'  # mantemos tudo como "compra"
            item.grupo           = tipo_compra
            item.descricao       = descricao
            item.valor           = valor
            item.forma_pagamento = forma_pgto
            db.session.commit()
            flash('Lançamento atualizado.', 'success')
        else:
            novo = LivroFinanceiro(
                data=data_fmt,
                tipo='compra',
                grupo=tipo_compra,
                descricao=descricao,
                valor=valor,
                forma_pagamento=forma_pgto,
                user_id=current_user.id
            )
            db.session.add(novo)
            db.session.commit()
            flash('Lançamento adicionado.', 'success')

        # volta mantendo os filtros atuais
        return redirect(request.referrer or url_for('admin_lancamentos'))

    # ---------- GET = listar com filtros ----------
    start_qs = request.args.get('start')  # yyyy-mm-dd ou dd/mm/aaaa
    end_qs   = request.args.get('end')

    # filtros (selects)
    tipo_sel  = (request.args.get('tipo')  or '').strip()    # "Tipo de compra"
    forma_sel = (request.args.get('forma') or '').strip()    # "Forma de Pgto"

    # datas do filtro
    dt_start = parse_ddmmyyyy(start_qs) if start_qs else start_default
    dt_end   = parse_ddmmyyyy(end_qs)   if end_qs   else end_default
    if dt_end < dt_start:
        dt_start, dt_end = dt_end, dt_start

    # carrega itens do usuário e aplica filtros
    itens = (LivroFinanceiro.query
                .filter_by(user_id=current_user.id)
                .order_by(LivroFinanceiro.id.desc())
                .all())

    itens = [
        i for i in itens
        if dt_start <= parse_ddmmyyyy(i.data) <= dt_end
           and (not tipo_sel  or (i.grupo or '').strip() == tipo_sel)
           and (not forma_sel or (i.forma_pagamento or '').strip() == forma_sel)
    ]

    # somatórios e agrupamentos para os cards simples
    total_periodo = sum(float(i.valor or 0) for i in itens)

    by_grupo, by_forma = {}, {}
    for i in itens:
        by_grupo[i.grupo] = by_grupo.get(i.grupo, 0.0) + float(i.valor or 0)
        by_forma[i.forma_pagamento] = by_forma.get(i.forma_pagamento, 0.0) + float(i.valor or 0)

    # listas para os selects do formulário
    tipos_compra = sorted(GRUPOS_PADRAO)
    try:
        formas_pgto = FORMAS_PGTO
    except NameError:
        formas_pgto = sorted([
            "Dinheiro","PagBank","Banco Inter","Itau","InfinitePay","Valori",
            "C6","Mercado Pago Alex","IFOOD","Cartão Credito","Mercado Pago Danuze",
        ])

    # ---------- PIVOT (Grupo -> Fornecedor/Descrição -> valores por dia) ----------
    # cabeçalho de datas (inclusive)
    dias = []
    d = dt_start
    while d <= dt_end:
        dias.append(d)
        d += timedelta(days=1)

    dias_br    = [d.strftime('%d/%m/%Y') for d in dias]  # p/ localizar o índice usando i.data (string dd/mm/aaaa)
    dias_label = [d.strftime('%d/%m')     for d in dias]  # p/ mostrar no cabeçalho

    from collections import defaultdict
    # grouped[group][desc] = [v_dia0, v_dia1, ...]
    grouped = defaultdict(lambda: defaultdict(lambda: [0.0] * len(dias)))

    for i in itens:
        # i.data já é dd/mm/aaaa
        try:
            idx = dias_br.index(i.data)
        except ValueError:
            continue  # segurança
        grupo = (i.grupo or 'OUTROS').strip() or 'OUTROS'
        desc  = (i.descricao or '—').strip() or '—'
        grouped[grupo][desc][idx] += float(i.valor or 0)

    # monta estrutura amigável pro template
    pivot_groups = []
    for grupo in sorted(grouped.keys(), key=str.lower):
        rows = []
        group_cols = [0.0] * len(dias)
        for desc, vals in sorted(grouped[grupo].items(), key=lambda kv: kv[0].lower()):
            total_row = sum(vals)
            for j, v in enumerate(vals):
                group_cols[j] += v
            rows.append({
                "desc": desc,
                "vals": vals,
                "total": total_row
            })
        pivot_groups.append({
            "grupo": grupo,
            "rows": rows,
            "totals": group_cols,
            "total": sum(group_cols)
        })

    # render
    return render_template(
        'admin_lancamentos.html',
        # filtros selecionados
        start_iso=dt_start.strftime('%Y-%m-%d'),
        end_iso=dt_end.strftime('%Y-%m-%d'),
        tipo_sel=tipo_sel,
        forma_sel=forma_sel,
        # selects
        TIPOS_COMPRA=tipos_compra,
        FORMAS_PGTO=formas_pgto,
        # dados "simples"
        itens=itens,
        total_periodo=total_periodo,
        by_grupo=sorted(by_grupo.items(), key=lambda x: x[0].lower()),
        by_forma=sorted(by_forma.items(), key=lambda x: x[0].lower()),
        # NOVO: pivot por dia (para a tabela estilo planilha)
        dias_label=dias_label,
        pivot_groups=pivot_groups,
    )


@app.route('/admin/lancamentos/<int:item_id>/delete', methods=['POST'])
@require_admin
def admin_lancamentos_delete(item_id):
    item = LivroFinanceiro.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        flash('Sem permissão.', 'danger')
        return redirect(url_for('admin_lancamentos'))
    db.session.delete(item)
    db.session.commit()
    flash('Lançamento removido.', 'success')
    return redirect(request.referrer or url_for('admin_lancamentos'))

# --- no topo do app.py ---
from collections import defaultdict
from datetime import datetime, timedelta

# ajuda com datas pt-BR "dd/mm/aaaa"
def br_to_date(s: str) -> datetime:
    return datetime.strptime(s, "%d/%m/%Y")

def date_to_br(d: datetime) -> str:
    return d.strftime("%d/%m/%Y")

def monday_of_week(anchor: datetime) -> datetime:
    return anchor - timedelta(days=anchor.weekday())

# grupos p/ lançamentos (exatamente como você pediu)
GRUPOS_PADRAO = [
    "ATACADISTA","BEBIDAS","COMBUSTIVEL","COMPRAS CARTÃO","CONGELADOS",
    "CONTAS FIXAS","EMBALAGENS","ENTREGADOR","FRIOS","FUNCIONARIO",
    "EMPRESTIMO","IFOOD","IMPOSTO / TAXAS","INDIRETOS","LARANJA / HORTA",
    "MERCADO","OUTROS","PÃO","VALE","ANUNCIOS","SORVETERIA"
]

# Tipos de compra (ordenado alfabeticamente)
TIPOS_COMPRA = sorted([
    "ANUNCIOS",
    "ATACADISTA",
    "BEBIDAS",
    "COMBUSTIVEL",
    "COMPRAS CARTÃO",
    "CONGELADOS",
    "CONTAS FIXAS",
    "EMBALAGENS",
    "EMPRESTIMO",
    "ENTREGADOR",
    "FRIOS",
    "FUNCIONARIO",
    "IFOOD",
    "IMPOSTO / TAXAS",
    "INDIRETOS",
    "LARANJA / HORTA",
    "MERCADO",
    "OUTROS",
    "PÃO",
    "SORVETERIA",
    "VALE",
])

FORMAS_BASE = [
    "Dinheiro",
    "PagBank",
    "Banco Inter",
    "Itau",                 # padronize: "Itau" vs "Itaú"
    "InfinitePay",
    "Valori",
    "C6",
    "Mercado Pago Alex",
    "IFOOD",
    "Cartão Credito",       # padronize: "Credito" vs "Crédito"
    "Mercado Pago Danuze",
]
FORMAS_PGTO = sorted(FORMAS_BASE)
# Se quiser preservar a ordem original em outro lugar, use FORMAS_BASE.


# mapeamento dos campos das maquininhas no RegistroDiario
MAQS = {
    "MP":     {"debito":"mp_debito","credito":"mp_credito","pix":"mp_pix"},
    "ITAU1":  {"debito":"itau1_debito","credito":"itau1_credito","pix":"itau1_pix"},
    "ITAU2":  {"debito":"itau2_debito","credito":"itau2_credito","pix":"itau2_pix"},
    "ITAU3":  {"debito":"itau3_debito","credito":"itau3_credito","pix":"itau3_pix"},
    "VALORI": {"debito":"valori_debito","credito":"valori_credito","pix":"valori_pix"},
    "INFPAY": {"debito":"infinitepay_debito","credito":"infinitepay_credito","pix":"infinitepay_pix"},
    "C6":     {"debito":None,"credito":None,"pix":"c6_pix"},  # C6 só PIX
}

# ---------- util p/ datas ----------
def parse_iso_or_br(s: str):
    if not s: return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None

def daterange_days(start: datetime, end: datetime):
    n = (end - start).days
    for i in range(n + 1):
        yield start + timedelta(days=i)

# ------------------------------------------------------------
# Helpers para o relatório por intervalo
# ------------------------------------------------------------
def parse_iso_or_br(s: str):
    """
    Converte 'YYYY-MM-DD' OU 'DD/MM/YYYY' em datetime.date.
    Retorna None se vazio/ruim.
    """
    if not s:
        return None
    s = s.strip()
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def daterange_days(d0, d1):
    """Itera de d0 até d1 (INCLUSIVO), ambos date."""
    from datetime import timedelta
    cur = d0
    while cur <= d1:
        yield cur
        cur = cur + timedelta(days=1)

# ------------------------------------------------------------
# ---------- RELATÓRIO POR INTERVALO ----------
# ------------------------------------------------------------
@app.route("/admin/relatorio")
@require_admin
def admin_relatorio():
    """
    Relatório por intervalo (datas em sequência). Mostra:
      - Cartões/PIX por maquininha (por dia)
      - Tabela de taxas por adquirente
      - Resumo por dia (por Tipo de compra) — pivot compacto dos lançamentos
    Parâmetros:
      start=YYYY-MM-DD   end=YYYY-MM-DD   (opcionais)
    """
    # intervalo padrão = semana atual (seg→dom)
    today = datetime.now()
    def_week_start = today - timedelta(days=today.weekday())
    def_week_end   = def_week_start + timedelta(days=6)

    # datas vindas do querystring (aceita ISO ou BR)
    def parse_iso_or_br(s):
        if not s:
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        return None

    start_qs = request.args.get("start")
    end_qs   = request.args.get("end")

    start = parse_iso_or_br(start_qs) or def_week_start
    end   = parse_iso_or_br(end_qs)   or def_week_end
    if end < start:
        start, end = end, start
    # (opcional) limita a 31 dias
    if (end - start).days > 31:
        end = start + timedelta(days=31)

    # lista de dias do intervalo
    dias = []
    d = start
    while d <= end:
        dias.append(d)
        d += timedelta(days=1)
    dias_br    = [d.strftime("%d/%m/%Y") for d in dias]
    dias_label = [d.strftime("%d/%m")     for d in dias]
    period     = f"{dias_label[0]} – {dias_label[-1]}"

    # ====== CARTÕES / PIX por maquininha (linhas) × dia (colunas) ======
    from collections import defaultdict

    MAQS = {
        "Mercado Pago": {"debito":"mp_debito","credito":"mp_credito","pix":"mp_pix"},
        "Itaú 1": {"debito":"itau1_debito","credito":"itau1_credito","pix":"itau1_pix"},
        "Itaú 2": {"debito":"itau2_debito","credito":"itau2_credito","pix":"itau2_pix"},
        "Itaú 3": {"debito":"itau3_debito","credito":"itau3_credito","pix":"itau3_pix"},
        "Valori": {"debito":"valori_debito","credito":"valori_credito","pix":"valori_pix"},
        "InfinitePay": {"debito":"infinitepay_debito","credito":"infinitepay_credito","pix":"infinitepay_pix"},
        "C6 (PIX CNPJ)": {"debito":None,"credito":None,"pix":"c6_pix"},
    }

    regs = RegistroDiario.query.filter(
        RegistroDiario.status=="fechado",
        RegistroDiario.data.in_(dias_br)
    ).all()

    regs_por_dia = defaultdict(list)
    for r in regs:
        regs_por_dia[r.data].append(r)

    def soma_campos(lista_reg, campo):
        if not campo: return 0.0
        return sum(float(getattr(r, campo) or 0.0) for r in lista_reg)

    linhas_maqs = []
    tot_por_meio = {"debito":[0.0]*len(dias), "credito":[0.0]*len(dias), "pix":[0.0]*len(dias)}

    for nome, campos in MAQS.items():
        for meio in ("debito", "credito", "pix"):
            if not campos.get(meio):
                continue
            col = []
            for i, dbr in enumerate(dias_br):
                v = soma_campos(regs_por_dia[dbr], campos[meio])
                col.append(v)
                tot_por_meio[meio][i] += v
            linhas_maqs.append({
                "label": f"{nome} {meio.capitalize()}",
                "valores": col,
                "total": sum(col)
            })

    # ====== Tabela de TAXAS por adquirente ======
    ACQ_DISPLAY = {
        "Mercado Pago":"Mercado Pago",
        "Itaú 1":"Itaú", "Itaú 2":"Itaú", "Itaú 3":"Itaú",
        "Valori":"Valori",
        "InfinitePay":"InfinitePay",
        "C6 (PIX CNPJ)":"C6 (PIX CNPJ)",
    }
    FEE_PERCENTS = {
        ("Mercado Pago","debito"):0.0199, ("Mercado Pago","credito"):0.0498, ("Mercado Pago","pix"):0.0049,
        ("Itaú","debito"):0.0097, ("Itaú","credito"):0.0270, ("Itaú","pix"):0.0000,
        ("Valori","debito"):0.0097, ("Valori","credito"):0.0270, ("Valori","pix"):0.0000,
        ("InfinitePay","debito"):0.0144, ("InfinitePay","credito"):0.0289, ("InfinitePay","pix"):0.0000,
        ("C6 (PIX CNPJ)","pix"):0.0000,
    }

    brand_raw = {
        "Mercado Pago":{"debito":0.0,"credito":0.0,"pix":0.0},
        "Itaú":{"debito":0.0,"credito":0.0,"pix":0.0},
        "Valori":{"debito":0.0,"credito":0.0,"pix":0.0},
        "InfinitePay":{"debito":0.0,"credito":0.0,"pix":0.0},
        "C6 (PIX CNPJ)":{"pix":0.0},
    }
    for r in regs:
        brand_raw["Mercado Pago"]["debito"] += float(r.mp_debito or 0)
        brand_raw["Mercado Pago"]["credito"]+= float(r.mp_credito or 0)
        brand_raw["Mercado Pago"]["pix"]    += float(r.mp_pix or 0)

        brand_raw["Itaú"]["debito"]  += float(r.itau1_debito or 0)+float(r.itau2_debito or 0)+float(r.itau3_debito or 0)
        brand_raw["Itaú"]["credito"] += float(r.itau1_credito or 0)+float(r.itau2_credito or 0)+float(r.itau3_credito or 0)
        brand_raw["Itaú"]["pix"]     += float(r.itau1_pix or 0)+float(r.itau2_pix or 0)+float(r.itau3_pix or 0)

        brand_raw["Valori"]["debito"]  += float(r.valori_debito or 0)
        brand_raw["Valori"]["credito"] += float(r.valori_credito or 0)
        brand_raw["Valori"]["pix"]     += float(r.valori_pix or 0)

        brand_raw["InfinitePay"]["debito"]  += float(r.infinitepay_debito or 0)
        brand_raw["InfinitePay"]["credito"] += float(r.infinitepay_credito or 0)
        brand_raw["InfinitePay"]["pix"]     += float(r.infinitepay_pix or 0)

        brand_raw["C6 (PIX CNPJ)"]["pix"]   += float(r.c6_pix or 0)

    tax_rows = []
    for brand, medias in brand_raw.items():
        total_brand_net = 0.0
        temp = []
        for meio, soma in medias.items():
            perc = FEE_PERCENTS.get((brand, meio), 0.0)
            taxa = round(soma * perc, 2)
            real = round(soma - taxa, 2)
            total_brand_net += real
            temp.append({"brand":brand,"meio":meio,"soma":soma,"percent":perc,"taxa":taxa,"real":real})
        for row in temp:
            row["total_brand_net"] = round(total_brand_net, 2)
            tax_rows.append(row)

    # ====== NOVO: Resumo por dia (por Tipo de compra) – pivot compacto ======
    # (Usa LivroFinanceiro; se quiser limitar por usuário, troque por .filter_by(user_id=current_user.id))
    lanca = LivroFinanceiro.query.all()

    grouped = defaultdict(lambda: defaultdict(lambda: [0.0]*len(dias)))
    for x in lanca:
        try:
            idx = dias_br.index(x.data)   # x.data está em dd/mm/aaaa
        except ValueError:
            continue
        grupo = (x.grupo or "OUTROS").strip() or "OUTROS"
        desc  = (x.descricao or "—").strip() or "—"
        grouped[grupo][desc][idx] += float(x.valor or 0)

    pivot_lanc = []
    for g, descs in sorted(grouped.items(), key=lambda kv: kv[0].lower()):
        rows = []
        totals_cols = [0.0]*len(dias)
        for dsc, vals in sorted(descs.items(), key=lambda kv: kv[0].lower()):
            total_row = sum(vals)
            if total_row <= 0:
                continue
            for i, v in enumerate(vals):
                totals_cols[i] += v
            rows.append({"desc": dsc, "vals": vals, "total": total_row})
        gtotal = sum(totals_cols)
        if gtotal <= 0:
            continue
        pivot_lanc.append({
            "grupo": g,
            "rows": rows,
            "totals": totals_cols,
            "total": gtotal
        })

    return render_template(
        "admin_relatorio_semana.html",
        # cabeçalho/intervalo
        dias_label=dias_label,
        period=period,
        start_iso=start.strftime("%Y-%m-%d"),
        end_iso=end.strftime("%Y-%m-%d"),
        # cartões
        linhas_maqs=linhas_maqs,
        tot_por_meio=tot_por_meio,
        # taxas
        tax_rows=tax_rows,
        # NOVO pivot de lançamentos
        pivot_lanc=pivot_lanc,
    )


# =========================
# Rotas Principais
# =========================
@app.route("/")
@login_required
def homepage():
    hoje = datetime.now().strftime('%d/%m/%Y')
    registro_aberto = RegistroDiario.query.filter_by(status='aberto', user_id=current_user.id).first()
    return render_template('index.html', registro_aberto=registro_aberto, data_hoje=hoje)

@app.route('/abertura', methods=['GET', 'POST'])
@login_required
def abertura():
    """
    GET: renderiza o formulário de abertura (abertura.html).
    POST:
      - Se JSON: mantém o comportamento anterior (usa valor_inicial do JSON).
      - Se FORM: soma os totais por denominação (v_0_05, v_50_0, etc) e cria o registro.
    Também impede abrir novo caixa se já houver um 'aberto' para o usuário.
    """
    if request.method == 'POST':
        # Impede abrir 2x
        registro_aberto = RegistroDiario.query.filter_by(status='aberto', user_id=current_user.id).first()
        if registro_aberto:
            msg = 'Já existe um caixa aberto. Por favor, feche-o antes de abrir um novo.'
            # Se veio via JSON, responde JSON; se veio via formulário, redireciona
            if request.is_json:
                flash(msg, 'info')
                return jsonify({'redirect_url': url_for('homepage')}), 200
            flash(msg, 'info')
            return redirect(url_for('homepage'))

        # --- POST via JSON (compatibilidade) ---
        if request.is_json:
            dados = request.get_json(silent=True) or {}
            valor_inicial = float(dados.get('valor_inicial', 0))  # confia no cliente, se quiser pode validar mais
            data_escolhida = coerce_to_br_date(dados.get('data_abertura'))
            novo_registro = RegistroDiario(
                data=data_escolhida,
                user_id=current_user.id,
                valor_inicial_caixa=valor_inicial
            )
            db.session.add(novo_registro)
            db.session.commit()
            flash('Caixa aberto com sucesso!', 'success')
            return jsonify({'redirect_url': url_for('homepage')}), 201

        # --- POST via FORM (novo fluxo com totais por denominação) ---
        soma = 0.0
        totais = {}
        for d in DENOMS:
            key = f"v_{str(d).replace('.', '_')}"
            valor = parse_brl_to_float(request.form.get(key, "0"))
            totais[key] = valor
            soma += valor

        # Hidden enviado pelo JS do template, mas o servidor prioriza a soma calculada
        valor_inicial_hidden = parse_brl_to_float(request.form.get("valor_inicial", "0"))
        valor_inicial = soma if soma > 0 else valor_inicial_hidden
        data_escolhida = coerce_to_br_date(request.form.get('data_abertura'))

        novo_registro = RegistroDiario(
                data=data_escolhida,
                user_id=current_user.id,
                valor_inicial_caixa=valor_inicial
            )
        
        db.session.add(novo_registro)
        db.session.commit()

        flash('Caixa aberto com sucesso!', 'success')
        return redirect(url_for('homepage'))

    # GET
    return render_template('abertura.html', denoms=DENOMS)

@app.route('/fechamento/<int:registro_id>', methods=['GET', 'POST'])
@login_required
def fechamento(registro_id):
    registro = RegistroDiario.query.get_or_404(registro_id)
    if registro.author.id != current_user.id:
        flash('Acesso não autorizado.', 'danger')
        return redirect(url_for('homepage'))

    if request.method == 'POST':
        dados = request.get_json(silent=True) or {}
        registro.status = 'fechado'
        registro.valor_final_caixa = dados.get('valor_final_caixa', 0)
        registro.ifood_vendas = dados.get('ifood_vendas', 0)
        registro.ifood_pedidos = dados.get('ifood_pedidos', 0)
        registro.ifood_cancelamento = dados.get('ifood_cancelamento', 0)
        registro.food99_vendas = dados.get('food99_vendas', 0)
        registro.food99_pedidos = dados.get('food99_pedidos', 0)
        registro.pedidos_balcao = dados.get('pedidos_balcao', 0)
        registro.pedidos_zap = dados.get('pedidos_zap', 0)
        registro.pedidos_vuca = dados.get('pedidos_vuca', 0)
        registro.taxa_entrega = dados.get('taxa_entrega', 0)
        registro.mp_debito = dados.get('mp_debito', 0)
        registro.mp_credito = dados.get('mp_credito', 0)
        registro.mp_pix = dados.get('mp_pix', 0)
        registro.itau1_debito = dados.get('itau1_debito', 0)
        registro.itau1_credito = dados.get('itau1_credito', 0)
        registro.itau1_pix = dados.get('itau1_pix', 0)
        registro.itau2_debito = dados.get('itau2_debito', 0)
        registro.itau2_credito = dados.get('itau2_credito', 0)
        registro.itau2_pix = dados.get('itau2_pix', 0)
        registro.itau3_debito = dados.get('itau3_debito', 0)
        registro.itau3_credito = dados.get('itau3_credito', 0)
        registro.itau3_pix = dados.get('itau3_pix', 0)
        registro.valori_debito = dados.get('valori_debito', 0)
        registro.valori_credito = dados.get('valori_credito', 0)
        registro.valori_pix = dados.get('valori_pix', 0)
        registro.infinitepay_debito = dados.get('infinitepay_debito', 0)
        registro.infinitepay_credito = dados.get('infinitepay_credito', 0)
        registro.infinitepay_pix = dados.get('infinitepay_pix', 0)
        registro.c6_pix = dados.get('c6_pix', 0)
        registro.vuca_delivery_dinheiro = dados.get('vuca_delivery_dinheiro', 0)
        registro.vuca_delivery_debito = dados.get('vuca_delivery_debito', 0)
        registro.vuca_delivery_credito = dados.get('vuca_delivery_credito', 0)
        registro.vuca_delivery_pix = dados.get('vuca_delivery_pix', 0)
        registro.vuca_balcao_dinheiro = dados.get('vuca_balcao_dinheiro', 0)
        registro.vuca_balcao_debito = dados.get('vuca_balcao_debito', 0)
        registro.vuca_balcao_credito = dados.get('vuca_balcao_credito', 0)
        registro.vuca_balcao_pix = dados.get('vuca_balcao_pix', 0)

        # Regrava as sangrias
        Sangria.query.filter_by(registro_id=registro.id).delete()
        for s in dados.get('sangrias', []):
            nova_sangria = Sangria(
                descricao=s.get('descricao', ''),
                valor=s.get('valor', 0),
                forma_pagamento=s.get('forma_pagamento', ''),
                registro_id=registro.id
            )
            db.session.add(nova_sangria)

        db.session.commit()
        flash('Fechamento de caixa salvo com sucesso!', 'success')
        return jsonify({
            'message': 'Fechamento salvo!',
            'redirect_url': url_for('resumo_dia', registro_id=registro.id)
        }), 200

    return render_template('fechamento.html', registro=registro)

from datetime import datetime, timedelta

@app.route('/resumo/<int:registro_id>')
@login_required
def resumo_dia(registro_id):
    registro = RegistroDiario.query.get_or_404(registro_id)
    # Admin pode ver qualquer registro; demais só o próprio
    if current_user.role != 'admin' and registro.author.id != current_user.id:
        return redirect(url_for('homepage'))

    # ---------- Cabeçalho (cards de cima) ----------
    data_obj = datetime.strptime(registro.data, '%d/%m/%Y')
    dias_semana_pt = {
        'Monday': 'Segunda-feira', 'Tuesday': 'Terça-feira', 'Wednesday': 'Quarta-feira',
        'Thursday': 'Quinta-feira', 'Friday': 'Sexta-feira', 'Saturday': 'Sábado', 'Sunday': 'Domingo'
    }
    dia_semana = dias_semana_pt.get(data_obj.strftime('%A'), data_obj.strftime('%A'))

    total_maquininhas = sum([
        registro.mp_debito, registro.mp_credito, registro.mp_pix,
        registro.itau1_debito, registro.itau1_credito, registro.itau1_pix,
        registro.itau2_debito, registro.itau2_credito, registro.itau2_pix,
        registro.itau3_debito, registro.itau3_credito, registro.itau3_pix,
        registro.valori_debito, registro.valori_credito, registro.valori_pix,
        registro.infinitepay_debito, registro.infinitepay_credito, registro.infinitepay_pix,
        registro.c6_pix
    ])

    # iFood líquido (80% do líquido vs cancelamento, como você usa no total do dia)
    vendas_ifood_liquido = (registro.ifood_vendas - registro.ifood_cancelamento) * 0.8

    # Dinheiro / conferência
    vendas_dinheiro_vuca = (registro.vuca_balcao_dinheiro or 0) + (registro.vuca_delivery_dinheiro or 0)
    sangrias_dinheiro = sum(
        s.valor for s in registro.sangrias
        if (s.forma_pagamento or '').lower() == 'dinheiro'
    )
    previsao_caixa   = vendas_dinheiro_vuca - sangrias_dinheiro
    entrada_dinheiro = (registro.valor_final_caixa or 0) - (registro.valor_inicial_caixa or 0)
    quebra_caixa     = entrada_dinheiro - previsao_caixa
    vendas_dinheiro_final = entrada_dinheiro + sangrias_dinheiro

    total_vendas = (
        total_maquininhas
        + vendas_ifood_liquido
        + (registro.food99_vendas or 0)
        + vendas_dinheiro_final
    )

    total_pedidos = (
        (registro.ifood_pedidos or 0) + (registro.food99_pedidos or 0) +
        (registro.pedidos_balcao or 0) + (registro.pedidos_zap or 0) + (registro.pedidos_vuca or 0)
    )
    ticket_medio = total_vendas / total_pedidos if total_pedidos > 0 else 0

    totais = {
        'total_pedidos': total_pedidos,
        'total_vendas': total_vendas,
        'ticket_medio': ticket_medio,
        'vendas_dinheiro_vuca': vendas_dinheiro_vuca,
        'sangrias_dinheiro': sangrias_dinheiro,
        'previsao_caixa': previsao_caixa,
        'entrada_dinheiro': entrada_dinheiro,
        'quebra_caixa': quebra_caixa,
        'vendas_dinheiro_final': vendas_dinheiro_final,
    }

    # Comparação semana passada (mantido)
    data_base = data_obj - timedelta(days=7)
    data_base_str = data_base.strftime("%d/%m/%Y")
    registro_base = RegistroDiario.query.filter_by(
        user_id=registro.user_id, status="fechado", data=data_base_str
    ).first()

    if registro_base:
        total_maqs_base = sum([
            registro_base.mp_debito, registro_base.mp_credito, registro_base.mp_pix,
            registro_base.itau1_debito, registro_base.itau1_credito, registro_base.itau1_pix,
            registro_base.itau2_debito, registro_base.itau2_credito, registro_base.itau2_pix,
            registro_base.itau3_debito, registro_base.itau3_credito, registro_base.itau3_pix,
            registro_base.valori_debito, registro_base.valori_credito, registro_base.valori_pix,
            registro_base.infinitepay_debito, registro_base.infinitepay_credito, registro_base.infinitepay_pix,
            registro_base.c6_pix
        ])
        vendas_ifood_liq_base = (registro_base.ifood_vendas - registro_base.ifood_cancelamento) * 0.8
        sangrias_dinheiro_base = sum(
            s.valor for s in registro_base.sangrias
            if (s.forma_pagamento or '').lower() == 'dinheiro'
        )
        entrada_dinheiro_base = (registro_base.valor_final_caixa or 0) - (registro_base.valor_inicial_caixa or 0)
        vendas_dinheiro_final_base = entrada_dinheiro_base + sangrias_dinheiro_base

        total_vendas_base = (
            total_maqs_base + vendas_ifood_liq_base +
            (registro_base.food99_vendas or 0) + vendas_dinheiro_final_base
        )
        wow_percent = ((total_vendas - total_vendas_base) / total_vendas_base) * 100 if total_vendas_base else 0
    else:
        total_vendas_base = None
        wow_percent = 0

    wow = {"base_data": data_base_str if registro_base else None,
           "base_vendas": total_vendas_base,
           "percent": wow_percent}

    # ---------- Detalhes novos ----------
    # Maquininhas separadas
    maqs = [
        {"nome": "Mercado Pago", "debito": registro.mp_debito, "credito": registro.mp_credito, "pix": registro.mp_pix},
        {"nome": "Itaú 1",       "debito": registro.itau1_debito, "credito": registro.itau1_credito, "pix": registro.itau1_pix},
        {"nome": "Itaú 2",       "debito": registro.itau2_debito, "credito": registro.itau2_credito, "pix": registro.itau2_pix},
        {"nome": "Itaú 3",       "debito": registro.itau3_debito, "credito": registro.itau3_credito, "pix": registro.itau3_pix},
        {"nome": "Valori",       "debito": registro.valori_debito, "credito": registro.valori_credito, "pix": registro.valori_pix},
        {"nome": "InfinitePay",  "debito": registro.infinitepay_debito, "credito": registro.infinitepay_credito, "pix": registro.infinitepay_pix},
        {"nome": "C6 (PIX CNPJ)","debito": 0.0, "credito": 0.0, "pix": registro.c6_pix},
    ]
    for m in maqs:
        m["debito"]  = float(m["debito"]  or 0)
        m["credito"] = float(m["credito"] or 0)
        m["pix"]     = float(m["pix"]     or 0)
        m["total"]   = round(m["debito"] + m["credito"] + m["pix"], 2)

    totais_meio = {
        "debito":  round(sum(m["debito"] for m in maqs), 2),
        "credito": round(sum(m["credito"] for m in maqs), 2),
        "pix":     round(sum(m["pix"] for m in maqs), 2),
    }

    # Pedidos sem marketplace detalhados + taxa de entrega
    pedidos_detalhe = {
        "balcao": int(registro.pedidos_balcao or 0),
        "zap":    int(registro.pedidos_zap or 0),
        "vuca":   int(registro.pedidos_vuca or 0),
        "taxa_entrega": float(registro.taxa_entrega or 0),
    }

    # iFood e 99
    ifood_info = {
        "pedidos": int(registro.ifood_pedidos or 0),
        "valor":   float((registro.ifood_vendas or 0) - (registro.ifood_cancelamento or 0)),
        "liquido": float(vendas_ifood_liquido or 0),  # pedido: "valor ifood já sem a taxa"
        "cancelado": float(registro.ifood_cancelamento or 0),
    }
    n99_info = {
        "pedidos": int(registro.food99_pedidos or 0),
        "valor":   float(registro.food99_vendas or 0),
    }

    # Extras para "totais por meio"
    extras_meio = {
        "dinheiro": vendas_dinheiro_final,
        "ifood_liquido": vendas_ifood_liquido,
        "food99": float(registro.food99_vendas or 0),
    }

    # Sangrias (lista e total)
    sangrias = [{"descricao": s.descricao, "valor": float(s.valor or 0), "forma": (s.forma_pagamento or '')}
                for s in registro.sangrias]
    sangrias_total = round(sum(s["valor"] for s in sangrias), 2)

    contexto = {
        "registro": registro,
        "dia_semana": dia_semana,
        "totais": totais,
        "wow": wow,

        # novos blocos
        "maqs": maqs,
        "totais_meio": totais_meio,
        "extras_meio": extras_meio,
        "pedidos_detalhe": pedidos_detalhe,
        "ifood_info": ifood_info,
        "n99_info": n99_info,
        "sangrias": sangrias,
        "sangrias_total": sangrias_total,
    }
    return render_template('resumo.html', **contexto)


@app.route('/historico')
@login_required
def historico():
    # Se admin, pode ver todos. Pode filtrar por usuário via ?u=<id> (ou 'all')
    user_filter = request.args.get('u', 'all')

    q = RegistroDiario.query.filter_by(status='fechado')
    if current_user.role != 'admin':
        # usuário comum: apenas os dele
        q = q.filter_by(user_id=current_user.id)
        users = None
        selected_user = None
    else:
        # admin: todos, com filtro opcional
        if user_filter and user_filter != 'all':
            try:
                q = q.filter_by(user_id=int(user_filter))
            except ValueError:
                pass
        # lista de usuários para o select
        users = User.query.order_by(User.username.asc()).all()
        selected_user = user_filter

    registros = q.all()
    # ordenar pela data (dd/mm/aaaa) mais recente primeiro
    registros_ordenados = sorted(
        registros,
        key=lambda r: datetime.strptime(r.data, '%d/%m/%Y'),
        reverse=True
    )

    return render_template(
        'historico.html',
        registros=registros_ordenados,
        users=users,                # None para não-admin
        selected_user=selected_user # 'all' | user_id | None
    )


from flask import request  # já deve estar importado no topo

@app.route('/admin/registros/<int:registro_id>/delete', methods=['POST'])
@require_admin
def admin_delete_registro(registro_id):
    reg = RegistroDiario.query.get_or_404(registro_id)

    # (Opcional) só permitir excluir fechados:
    if reg.status != 'fechado':
        flash('Só é possível excluir registros já fechados.', 'warning')
        return redirect(request.referrer or url_for('historico'))

    try:
        db.session.delete(reg)  # sangrias serão apagadas por cascade
        db.session.commit()
        flash('Fechamento excluído com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Não foi possível excluir: {e}', 'danger')

    return redirect(request.referrer or url_for('historico'))


# =========================
# Util (opcional): criar DB na primeira execução
# =========================
@app.cli.command("init-db")
def init_db():
    """flask init-db"""
    db.create_all()
    print("Banco criado.")

if __name__ == "__main__":
    # Cria o banco se ainda não existir
    if not os.path.exists(os.path.join(basedir, 'dados.sqlite')):
        with app.app_context():
            db.create_all()
    app.run(debug=True)
