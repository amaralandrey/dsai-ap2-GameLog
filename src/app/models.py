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
    # verificação de idade (IDA): o nascimento nunca sai do servidor; quem consulta usa a faixa
    nascimento = db.Column(db.Date, nullable=True)
    nascimento_confiavel = db.Column(db.Boolean, default=False, nullable=False)
    aguardando_responsavel = db.Column(db.Boolean, default=False, nullable=False)
    idade_contestada_em = db.Column(db.DateTime, nullable=True)
    suspensa_em = db.Column(db.DateTime, nullable=True)
    role = db.Column(db.String(10), default="jogador", nullable=False)
    session_version = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    @property
    def is_admin(self):
        return self.role == "admin"


class VerificacaoIdade(db.Model):
    """Histórico só de inserção (IDA-06); nunca guarda documento, foto nem número de documento."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False, index=True)
    metodo = db.Column(db.String(30), nullable=False)
    faixa_resultante = db.Column(db.String(30), nullable=False)
    confiavel = db.Column(db.Boolean, nullable=False)
    divergente = db.Column(db.Boolean, default=False, nullable=False)  # IDA-08
    created_at = db.Column(db.DateTime, default=now, nullable=False)


@db.event.listens_for(VerificacaoIdade, "before_update")
def _historico_imutavel(*_):
    raise ValueError("o histórico de verificações de idade é só de inserção")


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
    developer_id = db.Column(db.ForeignKey("developer.id"), nullable=False)
    removed_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(20), nullable=True)  # None = jogo fictício do seed
    external_id = db.Column(db.String(40), nullable=True)
    cover_url = db.Column(db.String(500), nullable=True)
    developer = db.relationship(Developer, backref="games")
    genres = db.relationship(Genre, secondary=game_genres, backref="games")
    platforms = db.relationship(Platform, secondary=game_platforms, backref="games")
    fontes = db.relationship("JogoFonte", back_populates="game", cascade="all, delete-orphan")

    @property
    def cover(self):
        return self.cover_url or f"/capa/{self.slug}.svg"

    @property
    def lojas(self):
        """Vínculos que são lojas, na ordem de LOJAS; rawg e arquivo só registram origem (FNT-04)."""
        return sorted((v for v in self.fontes if v.fonte in LOJAS), key=lambda v: LOJAS.index(v.fonte))


LOJAS = ("steam", "gog", "epic")
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


class Status(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "game_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    game_id = db.Column(db.ForeignKey("game.id"), nullable=False)
    value = db.Column(db.String(12), nullable=False)
    updated_at = db.Column(db.DateTime, default=now, nullable=False)
    game = db.relationship(Game)


STATUSES = {
    "planejado": "Planejado",
    "jogando": "Jogando",
    "jogado": "Jogado",
    "abandonado": "Abandonado",
}


class Review(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "game_id"),)
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


REPORT_REASONS = {
    "spam": "Spam",
    "ofensivo": "Ofensivo",
    "spoiler": "Spoiler sem aviso",
    "outro": "Outro",
}


class Report(db.Model):
    __table_args__ = (db.UniqueConstraint("review_id", "reporter_id"),)
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.ForeignKey("review.id"), nullable=False)
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
    kind = db.Column(db.String(20), nullable=False)  # avaliou|jogado|lista|lista_jogo
    game_id = db.Column(db.ForeignKey("game.id"), nullable=True)
    review_id = db.Column(db.ForeignKey("review.id"), nullable=True)
    list_id = db.Column(db.ForeignKey("game_list.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    user = db.relationship(User)
    game = db.relationship(Game)
    review = db.relationship(Review)
    list = db.relationship(GameList)


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
