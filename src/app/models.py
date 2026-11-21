from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.orm import validates

db = SQLAlchemy()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


game_genres = db.Table(
    "game_genres",
    db.Column("game_id", db.ForeignKey("game.id"), primary_key=True),
    db.Column("genre_id", db.ForeignKey("genre.id"), primary_key=True),
)
game_platforms = db.Table(
    "game_platforms",
    db.Column("game_id", db.ForeignKey("game.id"), primary_key=True),
    db.Column("platform_id", db.ForeignKey("platform.id"), primary_key=True),
)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), nullable=False)
    username_lower = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    display_name = db.Column(db.String(60), nullable=False)
    bio = db.Column(db.String(280), default="", nullable=False)
    role = db.Column(db.String(10), default="jogador", nullable=False)
    session_version = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    perfil_privado = db.Column(db.Boolean, default=False, nullable=False)  # DIA-11
    steam_id = db.Column(db.String(17), unique=True, nullable=True)  # SteamID64 vinculado (HRS-01)

    @property
    def is_admin(self):
        return self.role == "admin"


class Instancia(db.Model):
    """Uma linha por banco: muda quando o banco é recriado e invalida cookies da cópia anterior."""
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), nullable=False)


class LoginAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), index=True, nullable=False)
    at = db.Column(db.DateTime, default=now, nullable=False)


class NamedMixin:
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)


class Genre(NamedMixin, db.Model):
    pass


class Platform(NamedMixin, db.Model):
    pass


class Developer(NamedMixin, db.Model):
    pass


class Game(db.Model):
    __table_args__ = (db.UniqueConstraint("source", "external_id"),)
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(170), unique=True, nullable=False)
    synopsis = db.Column(db.Text, nullable=False)
    release_date = db.Column(db.Date, nullable=False)
    data_aproximada = db.Column(db.Boolean, default=False, nullable=False)  # só o ano é conhecido (GOG-09)
    developer_id = db.Column(db.ForeignKey("developer.id"), nullable=False)
    removed_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(20), nullable=True)  # None = jogo fictício do seed
    external_id = db.Column(db.String(40), nullable=True)
    cover_url = db.Column(db.String(500), nullable=True)
    mesclado_em_id = db.Column(db.ForeignKey("game.id"), nullable=True)  # jogo principal da mesclagem (MRG-08)
    developer = db.relationship(Developer, backref="games")
    genres = db.relationship(Genre, secondary=game_genres, backref="games")
    platforms = db.relationship(Platform, secondary=game_platforms, backref="games")
    fontes = db.relationship("JogoFonte", back_populates="game", cascade="all, delete-orphan")

    mesclado_em = db.relationship("Game", remote_side=[id])

    @property
    def cover(self):
        return self.cover_url or f"/capa/{self.slug}.svg"

    @property
    def principal(self):
        """Jogo que responde por este: o próprio, ou o fim da cadeia de mesclagens (MRG-08)."""
        game, vistos = self, set()
        while game.mesclado_em is not None and game.id not in vistos:
            vistos.add(game.id)
            game = game.mesclado_em
        return game

    @property
    def lojas(self):
        """Vínculos que são lojas, na ordem de LOJAS; rawg e arquivo só registram origem (FNT-04)."""
        return sorted((v for v in self.fontes if v.fonte in LOJAS), key=lambda v: LOJAS.index(v.fonte))


LOJAS = ("steam", "gog", "epic")
NOMES_DAS_LOJAS = {"steam": "Steam", "gog": "GOG", "epic": "Epic Games Store"}
SO_VINCULADAS = ("epic",)  # lojas sem fonte de importação: entram só como vínculo (issue #9)


class JogoFonte(db.Model):
    """Em quais fontes e lojas o jogo está; identidade fonte + id externo (FNT-04, IMP-04)."""
    __table_args__ = (db.UniqueConstraint("fonte", "external_id"), db.UniqueConstraint("game_id", "fonte"))
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False, index=True)
    fonte = db.Column(db.String(20), nullable=False)
    external_id = db.Column(db.String(80), nullable=False)
    url_loja = db.Column(db.String(500), nullable=True)
    ultima_sincronizacao = db.Column(db.DateTime, nullable=True)
    game = db.relationship(Game, back_populates="fontes")

    @validates("fonte")
    def _fonte_aceita(self, _, fonte):
        from .fontes import FONTES  # import tardio: fontes importa models

        if fonte not in FONTES and fonte not in SO_VINCULADAS:
            raise ValueError(f"fonte desconhecida: {fonte}")
        return fonte


PENDENCIA_STATUS = ("pendente", "mesclada", "ignorada")


class PendenciaMerge(db.Model):
    """Par de jogos talvez duplicados, para o admin decidir (MRG-03); o par é guardado com o menor id primeiro."""
    __table_args__ = (db.UniqueConstraint("jogo_a_id", "jogo_b_id"), db.CheckConstraint("jogo_a_id < jogo_b_id"))
    id = db.Column(db.Integer, primary_key=True)
    jogo_a_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    jogo_b_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    semelhanca = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(10), default="pendente", nullable=False)  # pendente|mesclada|ignorada
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    decidida_em = db.Column(db.DateTime, nullable=True)
    decidida_por = db.Column(db.ForeignKey("user.id"), nullable=True)
    jogo_a = db.relationship(Game, foreign_keys=[jogo_a_id])
    jogo_b = db.relationship(Game, foreign_keys=[jogo_b_id])


class Status(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "game_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    value = db.Column(db.String(12), nullable=False)
    updated_at = db.Column(db.DateTime, default=now, nullable=False)
    zerado_em = db.Column(db.Date, nullable=True, index=True)  # dia da sessão mais antiga com "zerei" (DIA-04)
    game = db.relationship(Game)


STATUSES = {
    "planejado": "Planejado",
    "jogando": "Jogando",
    "jogado": "Jogado",
    "abandonado": "Abandonado",
}


class Review(db.Model):
    # AUTOINCREMENT: o id vira origem do extrato de XP e não pode ser reaproveitado depois de apagar (PRG-03)
    __table_args__ = (db.UniqueConstraint("user_id", "game_id"), {"sqlite_autoincrement": True})
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    text = db.Column(db.String(5000), default="", nullable=False)
    spoiler = db.Column(db.Boolean, default=False, nullable=False)
    edited = db.Column(db.Boolean, default=False, nullable=False)
    hidden = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship(User)
    game = db.relationship(Game)
    comments = db.relationship(
        "Comment", order_by="(Comment.created_at, Comment.id)", cascade="all, delete-orphan", passive_deletes=True,
        back_populates="review",
    )


class Comment(db.Model):
    """Comentário numa avaliação (COM-01..06); apagar a avaliação apaga os comentários (COM-06)."""
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.ForeignKey("review.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship(User)
    review = db.relationship(Review, back_populates="comments")


class Like(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "review_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    review_id = db.Column(db.ForeignKey("review.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=True, index=True)  # curtidas antigas: sem data (RNK-02)


REPORT_REASONS = {
    "spam": "Spam",
    "ofensivo": "Ofensivo",
    "spoiler": "Spoiler sem aviso",
    "outro": "Outro",
}


class Report(db.Model):
    __table_args__ = (db.UniqueConstraint("review_id", "reporter_id"),
                      db.UniqueConstraint("alvo_tipo", "alvo_id", "reporter_id"))
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.ForeignKey("review.id"), nullable=True)  # só nas denúncias de avaliação
    alvo_tipo = db.Column(db.String(12), default="avaliacao", nullable=False)  # avaliacao|post_clube (DEN-01)
    alvo_id = db.Column(db.Integer, nullable=True)
    reporter_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    reason = db.Column(db.String(12), nullable=False)
    comment = db.Column(db.String(500), default="", nullable=False)
    status = db.Column(db.String(10), default="pendente", nullable=False)  # pendente|ocultada|rejeitada
    decided_by = db.Column(db.ForeignKey("user.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    review = db.relationship(Review)
    reporter = db.relationship(User, foreign_keys=[reporter_id])
    decider = db.relationship(User, foreign_keys=[decided_by])


class GameList(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), default="", nullable=False)
    public = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship(User)
    items = db.relationship("ListItem", order_by="ListItem.position", cascade="all, delete-orphan")


class ListItem(db.Model):
    __table_args__ = (db.UniqueConstraint("list_id", "game_id"),)
    id = db.Column(db.Integer, primary_key=True)
    list_id = db.Column(db.ForeignKey("game_list.id"), nullable=False)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    game = db.relationship(Game)


class ListaInteligente(db.Model):
    """Lista calculada por uma regra do catálogo de `listas_inteligentes.py` (LSA-01, LSA-03)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    nome = db.Column(db.String(80), nullable=False)
    publica = db.Column(db.Boolean, default=True, nullable=False)
    regra = db.Column(db.Text, nullable=False)  # {"tipo": ..., "parametros": {...}}
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship(User)


class Follow(db.Model):
    __table_args__ = (db.UniqueConstraint("follower_id", "followed_id"),)
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    followed_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    follower = db.relationship(User, foreign_keys=[follower_id])
    followed = db.relationship(User, foreign_keys=[followed_id])


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # avaliou|jogado|lista|lista_jogo|sessao|importacao|conquista|clube
    game_id = db.Column(db.ForeignKey("game.id"), nullable=True)
    review_id = db.Column(db.ForeignKey("review.id"), nullable=True)
    list_id = db.Column(db.ForeignKey("game_list.id"), nullable=True)
    dia = db.Column(db.Date, nullable=True)  # sessao: o dia jogado, uma atividade por jogo e dia (DIA-10)
    valor = db.Column(db.Integer, nullable=True)  # sessao: minutos do dia; importacao: sessões criadas (HRS-10)
    texto = db.Column(db.String(200), nullable=True)  # conquista ou clube: o que aconteceu, para o feed
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    user = db.relationship(User)
    game = db.relationship(Game)
    review = db.relationship(Review)
    list = db.relationship(GameList)


ORIGENS_SESSAO = ("manual", "csv", "steam")


class SessaoJogo(db.Model):
    """Uma sessão do diário (DIA-01); `origem` steam é um saldo acumulado da biblioteca (HRS-04)."""
    __table_args__ = {"sqlite_autoincrement": True}  # o id vira origem do extrato de XP (PRG-03)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False, index=True)
    jogada_em = db.Column(db.Date, nullable=False, index=True)
    duracao_min = db.Column(db.Integer, nullable=False)
    inicio = db.Column(db.Time, nullable=True)
    plataforma_id = db.Column(db.ForeignKey("platform.id"), nullable=True)
    zerou = db.Column(db.Boolean, default=False, nullable=False)
    anotacao = db.Column(db.String(1000), default="", nullable=False)
    spoiler = db.Column(db.Boolean, default=False, nullable=False)
    origem = db.Column(db.String(10), default="manual", nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    game = db.relationship(Game)
    plataforma = db.relationship(Platform)


class PreviaImportacao(db.Model):
    """Linhas de um CSV do diário já classificadas, à espera da confirmação do dono (HRS-07)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    linhas = db.Column(db.Text, nullable=False)  # JSON com cada linha e sua classificação
    created_at = db.Column(db.DateTime, default=now, nullable=False)


class Notificacao(db.Model):
    """Aviso para um usuário; criado só por `notificacoes.notificar` (NTF-01)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)
    ator_id = db.Column(db.ForeignKey("user.id"), nullable=True)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=True)
    review_id = db.Column(db.ForeignKey("review.id", ondelete="CASCADE"), nullable=True)
    texto = db.Column(db.String(200), default="", nullable=False)
    alvo_url = db.Column(db.String(300), default="", nullable=False)
    lida_em = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    ator = db.relationship(User, foreign_keys=[ator_id])
    game = db.relationship(Game)
    review = db.relationship(Review)


class PreferenciaNotificacao(db.Model):
    """Interruptores "no site" e "por e-mail" de um tipo de notificação (NTF-09)."""
    __table_args__ = (db.UniqueConstraint("user_id", "tipo"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    site = db.Column(db.Boolean, default=True, nullable=False)
    email = db.Column(db.Boolean, default=False, nullable=False)


class Conquista(db.Model):
    """Item do catálogo de conquistas; a regra é a dupla (`metrica`, `meta`) (GAM-01, GAM-03)."""
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(40), unique=True, nullable=False)
    nome = db.Column(db.String(60), nullable=False)
    descricao = db.Column(db.String(200), nullable=False)
    categoria = db.Column(db.String(12), nullable=False)
    raridade = db.Column(db.String(10), nullable=False)
    icone = db.Column(db.String(8), nullable=False)
    metrica = db.Column(db.String(40), nullable=False, index=True)
    meta = db.Column(db.Integer, nullable=False)
    secreta = db.Column(db.Boolean, default=False, nullable=False)
    por_tempo = db.Column(db.Boolean, default=False, nullable=False)
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    do_admin = db.Column(db.Boolean, default=False, nullable=False)  # criada pelo admin: o sincronizar não mexe


class UsuarioConquista(db.Model):
    """Progresso de um usuário numa conquista; `desbloqueada_em` nula enquanto bloqueada (GAM-01)."""
    __table_args__ = (db.UniqueConstraint("user_id", "conquista_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    conquista_id = db.Column(db.ForeignKey("conquista.id"), nullable=False)
    progresso = db.Column(db.Integer, default=0, nullable=False)
    desbloqueada_em = db.Column(db.DateTime, nullable=True)
    fixada = db.Column(db.Boolean, default=False, nullable=False)  # na estante do perfil (GAM-10)
    conquista = db.relationship(Conquista)


class PerfilProgressao(db.Model):
    """XP, nível e sequência semanal de um usuário, criado no primeiro XP (PRG-01)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), unique=True, nullable=False)
    xp_total = db.Column(db.Integer, default=0, nullable=False)
    nivel = db.Column(db.Integer, default=1, nullable=False)
    sequencia_semanas = db.Column(db.Integer, default=0, nullable=False)
    maior_sequencia = db.Column(db.Integer, default=0, nullable=False)
    ultima_semana_ativa = db.Column(db.String(8), nullable=True)  # AAAA-Www
    congelamentos = db.Column(db.Integer, default=0, nullable=False)


class ExtratoXP(db.Model):
    """Cada lançamento de XP, único por (usuário, origem); estorno e ajuste também entram aqui (PRG-01, PRG-03)."""
    __table_args__ = (db.UniqueConstraint("user_id", "origem_tipo", "origem_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    origem_tipo = db.Column(db.String(24), nullable=False)
    origem_id = db.Column(db.String(40), nullable=False)
    xp = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)


class Temporada(db.Model):
    """Janela de um ranking: os trimestres civis ou uma criada pelo admin (RNK-01, RNK-10)."""
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(60), nullable=False)
    slug = db.Column(db.String(70), unique=True, nullable=False)
    inicio = db.Column(db.Date, nullable=False)
    fim = db.Column(db.Date, nullable=False)
    encerrada_em = db.Column(db.DateTime, nullable=True)


class SnapshotRanking(db.Model):
    """Retrato imutável dos 100 primeiros de cada categoria no encerramento (RNK-08, RNK-09)."""
    __table_args__ = (db.UniqueConstraint("temporada_id", "categoria", "posicao"),)
    id = db.Column(db.Integer, primary_key=True)
    temporada_id = db.Column(db.ForeignKey("temporada.id"), nullable=False)
    categoria = db.Column(db.String(10), nullable=False)
    posicao = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    valor = db.Column(db.Integer, nullable=False)


class Desclassificacao(db.Model):
    """Usuário fora de uma categoria numa temporada, até ser revertido (RNK-10)."""
    id = db.Column(db.Integer, primary_key=True)
    temporada_id = db.Column(db.ForeignKey("temporada.id"), nullable=False)
    categoria = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    motivo = db.Column(db.String(200), nullable=False)
    admin_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    revertida_em = db.Column(db.DateTime, nullable=True)


class Clube(db.Model):
    """Clube temático (CLB-01); removido pelo admin é escondido, sem apagar nada (CLB-13)."""
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(60), nullable=False)
    nome_lower = db.Column(db.String(60), unique=True, nullable=False)
    slug = db.Column(db.String(70), unique=True, nullable=False)
    descricao = db.Column(db.String(500), default="", nullable=False)
    genero_id = db.Column(db.ForeignKey("genre.id"), nullable=True)
    publico = db.Column(db.Boolean, default=True, nullable=False)
    removido_em = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    genero = db.relationship(Genre)


class MembroClube(db.Model):
    """Papel de um usuário no clube; `situacao` pendente é um pedido de entrada em clube privado (CLB-03)."""
    __table_args__ = (db.UniqueConstraint("clube_id", "user_id"),)
    id = db.Column(db.Integer, primary_key=True)
    clube_id = db.Column(db.ForeignKey("clube.id"), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    papel = db.Column(db.String(10), default="membro", nullable=False)  # dono|moderador|membro
    situacao = db.Column(db.String(10), default="ativo", nullable=False)  # ativo|pendente|removido
    entrou_em = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship(User)


class JogoDoMes(db.Model):
    __table_args__ = (db.UniqueConstraint("clube_id", "mes"),)
    id = db.Column(db.Integer, primary_key=True)
    clube_id = db.Column(db.ForeignKey("clube.id"), nullable=False)
    mes = db.Column(db.String(7), nullable=False)  # AAAA-MM
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    game = db.relationship(Game)


class MetaClube(db.Model):
    """Meta coletiva medida pelo diário (CLB-06); `atingida_em` é gravada uma única vez (CLB-08)."""
    id = db.Column(db.Integer, primary_key=True)
    clube_id = db.Column(db.ForeignKey("clube.id"), nullable=False, index=True)
    tipo = db.Column(db.String(14), nullable=False)  # horas_jogo|horas_total|zerados_jogo
    alvo = db.Column(db.Integer, nullable=False)
    inicio = db.Column(db.Date, nullable=False)
    fim = db.Column(db.Date, nullable=False)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=True)
    atingida_em = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    game = db.relationship(Game)


class ContribuinteMeta(db.Model):
    """Quem tinha contribuído quando a meta foi atingida; conta para as conquistas de clube."""
    __table_args__ = (db.UniqueConstraint("meta_id", "user_id"),)
    id = db.Column(db.Integer, primary_key=True)
    meta_id = db.Column(db.ForeignKey("meta_clube.id"), nullable=False)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)


class PostClube(db.Model):
    """Post do mural (CLB-10); moderadores ocultam com motivo, e 3 denúncias pendentes ocultam sozinhas (CLB-11)."""
    id = db.Column(db.Integer, primary_key=True)
    clube_id = db.Column(db.ForeignKey("clube.id"), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    texto = db.Column(db.String(1000), nullable=False)
    spoiler = db.Column(db.Boolean, default=False, nullable=False)
    oculto = db.Column(db.Boolean, default=False, nullable=False)
    motivo_ocultacao = db.Column(db.String(200), default="", nullable=False)
    oculto_por = db.Column(db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    user = db.relationship(User, foreign_keys=[user_id])


class Dismissed(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "game_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(60), nullable=False)
    detail = db.Column(db.String(300), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    admin = db.relationship(User)


def visible_games():
    """Jogos não removidos (CAT-07)."""
    return Game.query.filter(Game.removed_at.is_(None))


def game_stats(game_id):
    """Média (1 casa), total e histograma 0-10, ignorando avaliações ocultas (PAG-02..04)."""
    rows = (
        db.session.query(Review.score, func.count())
        .filter(Review.game_id == game_id, Review.hidden.is_(False))
        .group_by(Review.score)
        .all()
    )
    hist = {i: 0 for i in range(11)}
    hist.update(dict(rows))
    total = sum(hist.values())
    avg = round(sum(k * v for k, v in hist.items()) / total, 1) if total else None
    return {"avg": avg, "total": total, "hist": hist}


def avg_subquery():
    """Subquery (game_id, avg, n) para ordenar/filtrar por nota sem N+1."""
    return (
        db.session.query(
            Review.game_id.label("gid"),
            func.avg(Review.score).label("avg"),
            func.count().label("n"),
        )
        .filter(Review.hidden.is_(False))
        .group_by(Review.game_id)
        .subquery()
    )
