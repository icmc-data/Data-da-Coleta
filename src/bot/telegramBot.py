import asyncio
import logging
import os
import time
import datetime
from urllib.parse import urljoin

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from decorators import rate_limit_handler

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", 0))
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
GROUP_LINK = os.getenv("GROUP_LINK")
PATH_IMAGES = os.getenv("PATH_IMAGES", "./images")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation state
ASK_TOPIC_NAME = range(1)

# --- Admin cache ---
# Avoids a Telegram API call on every message in the ranking topic.
_admin_cache: dict[int, tuple[set[int], float]] = {}
ADMIN_CACHE_TTL = 300.0  # seconds


async def _get_admin_ids(bot, chat_id: int) -> set[int]:
    now = time.monotonic()
    cached = _admin_cache.get(chat_id)
    if cached and (now - cached[1]) < ADMIN_CACHE_TTL:
        return cached[0]
    admins = await bot.get_chat_administrators(chat_id)
    admin_ids = {a.user.id for a in admins}
    _admin_cache[chat_id] = (admin_ids, now)
    return admin_ids


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the main menu with joinable teams."""
    http: httpx.AsyncClient = context.bot_data['http']
    try:
        response = await http.get("/teams/")
        if response.status_code != 200:
            await update.message.reply_text("❌ Erro ao carregar os times do servidor.")
            return

        teams = response.json()
        joinable_teams = [t for t in teams if t.get("thread_id")]

        if not joinable_teams:
            await update.message.reply_text("Nenhum grupo foi criado ainda 😭😭 Porque não criar o seu próprio?")
            return

        emojis = ["🔥", "🗻", "🪓", "🌎"]
        keyboard = [
            [InlineKeyboardButton(f"{emojis[i]} {team['name']}", callback_data=f"join_team_{team['id']}")]
            for i, team in enumerate(sorted(joinable_teams, key=lambda t: t['name']))
        ]
        await update.message.reply_photo(
            photo=open(os.path.join(PATH_IMAGES, 'hamster.jpg'), 'rb'),
            caption="DATA 🤝 SEMCOMP \n\nSelecione abaixo sua casa do overflow para se registrar e participar do Data Da Coleta",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Error in start handler: {e}")


async def join_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers a user as a participant in the chosen team."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    team_id = int(query.data.split("_")[-1])
    http: httpx.AsyncClient = context.bot_data['http']

    try:
        reg_response = await http.post("/participants/", json={
            "id": str(user.id),
            "name": user.full_name,
            "team_id": team_id,
        })

        team_response = await http.get(f"/teams/{team_id}")
        if team_response.status_code != 200:
            await query.edit_message_caption("❌ Erro ao obter detalhes do time.")
            return

        team_data = team_response.json()
        team_name = team_data.get("name")
        thread_id = team_data.get("thread_id")

        if not thread_id:
            await query.edit_message_caption(f"✅ Você foi registrado no time '{team_name}', mas não há tópico associado.")
            return

        topic_link = urljoin(f"{GROUP_LINK}/", str(thread_id))
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Ir para o grupo!", url=topic_link)]])

        if reg_response.status_code == 201:
            logger.info(f"User {user.username} registered for team '{team_name}' (ID: {team_id}).")
            await query.edit_message_caption(
                f"✅ Você está pronto para representar sua casa {team_name}! Junte amigos e boa aventura!",
                reply_markup=reply_markup,
            )
        else:
            await query.edit_message_caption(
                f"Aqui está o link para '{team_name}':",
                reply_markup=reply_markup,
            )

    except httpx.RequestError as e:
        logger.error(f"HTTP error in join_team: {e}")
        await query.edit_message_caption("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        logger.error(f"Unexpected error in join_team: {e}")
        await query.edit_message_caption("❌ Um erro inesperado aconteceu.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ação Cancelada. Use /start para ver o menu principal.")
    return ConversationHandler.END


@rate_limit_handler
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message.is_topic_message or not message.message_thread_id:
        return
    await message.reply_text(
        "❗ Para salvar localização, envie a imagem como **Arquivo/Documento** (não como foto). "
        "Toque no clipe > Arquivo > selecione a imagem original. Depois reenvie aqui."
    )


@rate_limit_handler
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles JPEG/HEIC document submissions from users within a group topic."""
    message = update.message
    user = update.effective_user
    http: httpx.AsyncClient = context.bot_data['http']

    if not message.is_topic_message or not message.message_thread_id:
        return

    document = message.document
    if document.mime_type not in ['image/jpeg', 'image/heic']:
        await message.reply_text("Por favor, envie imagens JPEG ou HEIC.")
        return

    try:
        doc_file = await context.bot.get_file(document.file_id)
        doc_bytes = await doc_file.download_as_bytearray()

        response = await http.post(
            "/submissions/",
            files={'photo': (document.file_name, bytes(doc_bytes))},
            data={'participant_id': str(user.id), 'thread_id': message.message_thread_id},
        )

        if response.status_code == 201:
            logger.info(f"User {user.username} submitted image in topic {message.message_thread_id}.")
            await message.reply_text("✅ Imagem submetida com sucesso! Aguarde uns segundinhos até a imagem ser processada.")
        elif response.status_code == 403:
            logger.warning(f"User {user.username} submitted to wrong team in topic {message.message_thread_id}.")
            await message.reply_text("❌ Ô zé, vc tá mandando a foto pra casa errada.")
        elif response.status_code == 404:
            if "Participant" in response.text:
                await message.reply_text("❌ Vc parece não tá registrado ainda. Manda mensagem pro nosso mano @DataDaColeta_Bot pra se registrar.")
            else:
                await message.reply_text("❌ Esse canal não é pra mandar fotos 😭😭😭😭😭.")
        else:
            logger.error(f"Unexpected backend response {response.status_code}: {response.text}")
            await message.reply_text(f"❌ Deu um erro aí. Perdoar 🙏🙏. Erro: {response.text}")

    except Exception as e:
        logger.error(f"Error handling document for user {user.id} in topic {message.message_thread_id}: {e}", exc_info=True)
        await message.reply_text("❌ Algum trem quebrou aqui :( Tenta de novo mais tarde, pufavo.")


async def remove_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removes points from the team associated with the current topic (admin only)."""
    user = update.effective_user
    message = update.message
    http: httpx.AsyncClient = context.bot_data['http']

    if not message.is_topic_message or not message.message_thread_id:
        return

    try:
        admin_ids = await _get_admin_ids(context.bot, update.effective_chat.id)
        if user.id not in admin_ids:
            await message.reply_text("❌ Seu safadinho. Apenas administradores podem remover pontos 😤😤")
            return

        if not context.args:
            await message.reply_text("Esqueceu, zé? Precisa botar a quantidade de pontos depois do comando. Ex: /remove_points 20")
            return

        points_to_remove = int(context.args[0])

        get_team_response = await http.get(f"/teams/by_thread/{message.message_thread_id}")
        if get_team_response.status_code != 200:
            await message.reply_text(f"❌ Erro ao encontrar o time para este chat: {get_team_response.text}")
            return

        team_id = get_team_response.json()["id"]
        remove_response = await http.patch(f"/teams/{team_id}/score", json={"points": points_to_remove})

        if remove_response.status_code == 200:
            await message.reply_text(f"✅ {points_to_remove} pontos foram removidos do time.")
        else:
            await message.reply_text(f"❌ Erro ao remover os pontos: {remove_response.text}")

    except (IndexError, ValueError):
        await message.reply_text("Comando inválido. Use /remove_points <pontos>.")
    except httpx.RequestError as e:
        logger.error(f"HTTP error in remove_points: {e}")
        await message.reply_text(f"❌ Erro de comunicação com o servidor: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in remove_points: {e}")
        await message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")


async def export_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Asks admin for the export format."""
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != 'supergroup':
        await update.message.reply_text("Este comando só pode ser usado em um chat de grupo.")
        return

    try:
        admin_ids = await _get_admin_ids(context.bot, chat.id)
        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem exportar os dados.")
            return

        keyboard = [
            [InlineKeyboardButton("CSV", callback_data="export_csv")],
            [InlineKeyboardButton("JSON", callback_data="export_json")],
        ]
        await update.message.reply_text("Selecione o formato:", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Error in export_data_command: {e}")
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetches the export from the backend and sends it to the user."""
    query = update.callback_query
    await query.answer()
    http: httpx.AsyncClient = context.bot_data['http']

    fmt = query.data.split("_")[-1]
    try:
        response = await http.get(f"/export/{fmt}")
        if response.status_code == 200:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=response.content,
                filename=f"submissions.{fmt}",
            )
        else:
            await query.edit_message_text(f"❌ Erro ao exportar os dados: {response.text}")
    except Exception as e:
        logger.error(f"Error in export_data: {e}")
        await query.edit_message_text(f"❌ Um erro inesperado aconteceu: {e}")


async def create_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Creates a new event (admin only, supergroup)."""
    user = update.effective_user
    chat = update.effective_chat
    http: httpx.AsyncClient = context.bot_data['http']

    if chat.type != 'supergroup':
        await update.message.reply_text("Este comando só pode ser usado em um chat de grupo.")
        return

    try:
        admin_ids = await _get_admin_ids(context.bot, chat.id)
        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem criar novos eventos.")
            return

        response = await http.post("/events/", json={"date": datetime.datetime.utcnow().isoformat()})
        if response.status_code == 201:
            event = response.json()
            await update.message.reply_text(f"✅ Novo evento criado! ID: {event['id']}, Data: {event['date']}")
            logger.info(f"Event {event['id']} created by admin {user.username}.")
        else:
            await update.message.reply_text(f"❌ Erro ao criar o evento: {response.text}")

    except httpx.RequestError as e:
        logger.error(f"HTTP error in create_event_command: {e}")
        await update.message.reply_text("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        logger.error(f"Unexpected error in create_event_command: {e}")
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")


# --- RankingManager ---

LEGACY_THREAD_ID_FILE = "src/bot/ranking_thread_id.txt"


class RankingManager:
    def __init__(self, application: Application, http: httpx.AsyncClient):
        self.application = application
        self.http = http
        self.ranking_thread_id: int | None = None
        self.event_id: int | None = None
        self.last_ranking_message_id: int | None = None
        self.last_ranking_text: str | None = None

    async def setup(self):
        await self._load_thread_id()

        if self.ranking_thread_id:
            # Verify the stored topic still exists in Telegram before trusting it
            if not await self._topic_exists(self.ranking_thread_id):
                logger.warning(
                    f"Stored ranking thread_id {self.ranking_thread_id} no longer exists in Telegram. "
                    "Creating a new Ranking topic."
                )
                self.ranking_thread_id = None

        if not self.ranking_thread_id:
            await self._create_ranking_topic()

        if self.ranking_thread_id:
            self.application.add_handler(
                MessageHandler(filters.ChatType.SUPERGROUP, self._delete_non_admin_message)
            )
            scheduler = AsyncIOScheduler()
            scheduler.add_job(self.show_ranking, 'interval', minutes=1)
            scheduler.start()

    async def _topic_exists(self, thread_id: int) -> bool:
        """
        Verify a forum topic is still accessible by sending and immediately deleting
        a probe message. Returns False if the topic was deleted or is inaccessible.
        """
        try:
            msg = await self.application.bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=thread_id,
                text=".",
            )
            await msg.delete()
            return True
        except Exception as e:
            logger.warning(f"Topic {thread_id} probe failed: {e}")
            return False

    async def _load_thread_id(self):
        """Load ranking_thread_id: try DB first, then legacy file as fallback."""
        await self._load_from_db()
        if self.ranking_thread_id:
            return

        # Fallback: legacy flat file from before DB persistence was added
        try:
            with open(LEGACY_THREAD_ID_FILE, "r") as f:
                thread_id = int(f.read().strip())
            self.ranking_thread_id = thread_id
            logger.info(
                f"Loaded ranking thread_id {thread_id} from legacy file. "
                "Saving to DB so this file won't be needed again."
            )
            await self._save_to_db()
        except (FileNotFoundError, ValueError):
            logger.info("No ranking thread_id found in DB or legacy file. Will create a new topic.")

    async def _load_from_db(self):
        """Load ranking_thread_id from the latest event in the backend."""
        try:
            response = await self.http.get("/events/")
            if response.status_code == 200 and response.json():
                event = response.json()[0]
                self.event_id = event["id"]
                self.ranking_thread_id = event.get("ranking_thread_id")
                if self.ranking_thread_id:
                    logger.info(f"Loaded ranking thread_id {self.ranking_thread_id} from DB (event {self.event_id}).")
                else:
                    logger.info(f"No ranking thread_id in DB for event {self.event_id}.")
        except Exception as e:
            logger.error(f"Failed to load ranking thread_id from DB: {e}")

    async def _save_to_db(self):
        """Persist ranking_thread_id to the event record in the backend."""
        if not self.event_id:
            logger.error("Cannot save ranking thread_id: no event_id available.")
            return
        try:
            response = await self.http.patch(
                f"/events/{self.event_id}/ranking_thread",
                json={"ranking_thread_id": self.ranking_thread_id},
            )
            if response.status_code == 200:
                logger.info(f"Saved ranking thread_id {self.ranking_thread_id} to DB.")
            else:
                logger.error(f"Failed to save ranking thread_id: {response.text}")
        except Exception as e:
            logger.error(f"Error saving ranking thread_id to DB: {e}")

    async def _create_ranking_topic(self):
        try:
            new_topic = await self.application.bot.create_forum_topic(chat_id=CHAT_ID, name="Ranking")
            self.ranking_thread_id = new_topic.message_thread_id
            await self._save_to_db()
            logger.info(f"Created 'Ranking' topic with thread_id {self.ranking_thread_id}.")
        except Exception as e:
            if "topic with the same name already exists" in str(e):
                logger.warning(
                    "A 'Ranking' topic already exists but its thread_id is not in the DB. "
                    "Find the thread_id and call PATCH /events/{id}/ranking_thread manually, "
                    "then restart the bot."
                )
            else:
                logger.error(f"Failed to create 'Ranking' topic: {e}")

    async def _delete_non_admin_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.message_thread_id != self.ranking_thread_id:
            return
        admin_ids = await _get_admin_ids(self.application.bot, CHAT_ID)
        if update.message.from_user.id not in admin_ids:
            await update.message.delete()

    async def show_ranking(self):
        if not self.ranking_thread_id:
            logger.warning("Ranking thread_id not set. Skipping ranking update.")
            return
        try:
            response = await self.http.get("/ranking/")
            if response.status_code != 200:
                logger.error(f"Failed to fetch ranking: {response.text}")
                return

            teams = response.json()
            if not teams:
                ranking_message = "Ainda não há pontuação no ranking."
            else:
                ranking_message = "🏆 **Ranking da Competição** 🏆\n\n"
                for i, team in enumerate(teams):
                    ranking_message += f"{i+1}º - {team['name']}: {team['score']} pontos\n"

            if ranking_message == self.last_ranking_text:
                return

            if self.last_ranking_message_id:
                await self.application.bot.edit_message_text(
                    chat_id=CHAT_ID,
                    message_id=self.last_ranking_message_id,
                    text=ranking_message,
                    parse_mode="Markdown",
                )
            else:
                message = await self.application.bot.send_message(
                    chat_id=CHAT_ID,
                    message_thread_id=self.ranking_thread_id,
                    text=ranking_message,
                    parse_mode="Markdown",
                )
                self.last_ranking_message_id = message.message_id

            self.last_ranking_text = ranking_message

        except httpx.RequestError as e:
            logger.error(f"HTTP error fetching ranking: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in show_ranking: {e}")


# --- Create topic conversation ---

async def create_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the create-team conversation (admin only, private chat)."""
    user = update.effective_user
    try:
        admin_ids = await _get_admin_ids(context.bot, CHAT_ID)
        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem criar novos times.")
            return ConversationHandler.END

        await update.message.reply_text("Qual o nome do novo time que você quer criar?")
        return ASK_TOPIC_NAME

    except Exception as e:
        logger.error(f"Error in create_topic_command: {e}")
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")
        return ConversationHandler.END


async def create_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Creates a team in the DB, a Telegram topic, and links them atomically."""
    topic_name = update.message.text
    user = update.effective_user
    http: httpx.AsyncClient = context.bot_data['http']

    try:
        # Step 1: Get the latest event
        events_response = await http.get("/events/")
        if events_response.status_code != 200 or not events_response.json():
            await update.message.reply_text("❌ Não há eventos ativos. Peça para um admin criar um novo evento.")
            return ConversationHandler.END

        latest_event_id = events_response.json()[0]["id"]

        # Step 2: Create the team in the backend
        team_response = await http.post("/teams/", json={"name": topic_name, "event_id": latest_event_id})
        if team_response.status_code == 400 and "already exists" in team_response.text:
            await update.message.reply_text(f"❌ O time '{topic_name}' já existe. Por favor, escolha outro nome.")
            return ConversationHandler.END
        if team_response.status_code != 201:
            await update.message.reply_text(f"❌ Erro ao criar o time no servidor: {team_response.text}")
            return ConversationHandler.END

        team_id = team_response.json()["id"]
        logger.info(f"Team '{topic_name}' created in backend with ID {team_id}.")

        # Step 3: Register the creator as a participant
        part_response = await http.post("/participants/", json={
            "id": str(user.id),
            "name": user.full_name,
            "team_id": team_id,
        })
        if part_response.status_code != 201:
            logger.error(f"Failed to register participant {user.username} for team '{topic_name}': {part_response.text}")

        # Step 4: Create the Telegram topic
        new_topic = await context.bot.create_forum_topic(chat_id=CHAT_ID, name=topic_name)
        thread_id = new_topic.message_thread_id

        # Step 5: Link the team to the Telegram topic
        link_response = await http.patch(f"/teams/{team_id}", json={"thread_id": thread_id})
        if link_response.status_code != 200:
            # Partial failure: topic was created but not linked — try to roll back the topic
            logger.error(f"Failed to link team {team_id} to thread {thread_id}: {link_response.text}")
            try:
                await context.bot.delete_forum_topic(chat_id=CHAT_ID, message_thread_id=thread_id)
                await update.message.reply_text(
                    f"❌ Erro ao associar o grupo ao servidor. A operação foi revertida. Tente novamente."
                )
            except Exception as cleanup_err:
                logger.error(f"Failed to delete orphaned topic {thread_id}: {cleanup_err}")
                await update.message.reply_text(
                    f"❌ Erro crítico: o tópico Telegram '{topic_name}' foi criado (thread_id={thread_id}) "
                    f"mas não foi salvo no banco. Anote esse ID e contate um admin para corrigir manualmente."
                )
            return ConversationHandler.END

        # Step 6: Send welcome message to the new topic
        await context.bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=thread_id,
            text=(
                f'👋 **Seja bem-vindo ao grupo "{topic_name}"!**\n\n'
                f"O grupo foi criado e você foi registrado como o primeiro participante, {user.mention_markdown()}."
            ),
            parse_mode="Markdown",
        )

        # Step 7: Confirm to the admin
        link_chat_id = str(CHAT_ID).replace("-100", "")
        topic_link = f"https://t.me/c/{link_chat_id}/{thread_id}"
        await update.message.reply_text(
            f"✅ Maravilha! O grupo \"{topic_name}\" foi criado e você foi registrado nele.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Ir para o grupo!", url=topic_link)]]),
        )
        logger.info(f"User {user.username} created topic '{topic_name}' (thread_id={thread_id}).")

    except httpx.RequestError as e:
        logger.error(f"HTTP error in create_topic: {e}")
        await update.message.reply_text("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        logger.error(f"Unexpected error in create_topic: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")

    return ConversationHandler.END


# --- Application lifecycle ---

async def _post_init(application: Application) -> None:
    """Set up shared HTTP client and ranking manager before polling starts."""
    http = httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0)
    application.bot_data['http'] = http

    ranking_manager = RankingManager(application, http)
    application.bot_data['ranking_manager'] = ranking_manager
    await ranking_manager.setup()


async def _post_shutdown(application: Application) -> None:
    """Clean up shared resources on shutdown."""
    http: httpx.AsyncClient = application.bot_data.get('http')
    if http:
        await http.aclose()


def main() -> None:
    if not TELEGRAM_TOKEN or CHAT_ID == 0:
        logger.error("TELEGRAM_TOKEN or CHAT_ID not set correctly in .env")
        return

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    private_filter = filters.ChatType.PRIVATE
    group_topic_filter = filters.ChatType.SUPERGROUP & filters.IS_TOPIC_MESSAGE

    application.add_handler(CommandHandler("start", start, filters=private_filter))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(join_team, pattern=r"^join_team_"))
    application.add_handler(MessageHandler(filters.PHOTO & group_topic_filter, handle_photo))
    application.add_handler(MessageHandler(filters.Document.IMAGE & group_topic_filter, handle_document))
    application.add_handler(CommandHandler("create_event", create_event_command, filters=filters.ChatType.SUPERGROUP))
    application.add_handler(CommandHandler("remove_points", remove_points, filters=filters.ChatType.SUPERGROUP))
    application.add_handler(CommandHandler("export", export_data_command, filters=filters.ChatType.SUPERGROUP))
    application.add_handler(CallbackQueryHandler(export_data, pattern="^export_csv$"))
    application.add_handler(CallbackQueryHandler(export_data, pattern="^export_json$"))
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('create_topic', create_topic_command, filters=private_filter)],
        states={ASK_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_topic)]},
        fallbacks=[CommandHandler('cancel', cancel)],
    ))

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
