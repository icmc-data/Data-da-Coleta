import os
import logging
import json
import datetime
import asyncio
from urllib.parse import urljoin
from dotenv import load_dotenv
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Defaults,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Configuration ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", 0))
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
GROUP_LINK = os.getenv("GROUP_LINK")
PATH_IMAGES = os.getenv("PATH_IMAGES", "./images")

# --- Logging ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Conversation States ---
ASK_TOPIC_NAME = range(1)

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays the main menu.
    - Sends a new photo message if triggered by /start.
    - Edits the existing message if triggered by a 'Back' button.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{BACKEND_URL}/teams/")
            if response.status_code != 200:
                await update.message.reply_text(text="❌ Erro ao carregar os times do servidor.")
                return
            
            teams = response.json()
            
            # Filter for teams that have a thread_id, meaning they are associated with a Telegram topic
            joinable_teams = [team for team in teams if team.get("thread_id")]

            if not joinable_teams:
                await update.message.reply_text(
                    text="Nenhum grupo foi criado ainda 😭😭 Porque não criar o seu próprio?"
                )
                return

            emojis = ["🔥", "🗻", "🪓", "🌎"]

            keyboard = []
            for i, team in enumerate(sorted(joinable_teams, key=lambda t: t['name'])):
                emoji = emojis[i]
                button = InlineKeyboardButton(f"{emoji} {team['name']}", callback_data=f"join_team_{team['id']}")
                keyboard.append([button])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_photo(
                photo=open(os.path.join(PATH_IMAGES, 'hamster.jpg'), 'rb'),
                caption="Selecione um dos grupos abaixo para se registrar e participar ➡️", 
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"An error occurred in show_join_menu: {e}")

async def join_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles a user's request to join a team and registers them as a participant."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    team_id = int(query.data.split("_")[-1])

    try:
        async with httpx.AsyncClient() as client:
            # Register the participant
            participant_data = {
                "id": str(user.id),
                "name": user.full_name,
                "team_id": team_id
            }
            reg_response = await client.post(f"{BACKEND_URL}/participants/", json=participant_data)

            # Fetch team details to get thread_id and name
            team_response = await client.get(f"{BACKEND_URL}/teams/{team_id}")
            if team_response.status_code != 200:
                await query.edit_message_caption("❌ Erro ao obter detalhes do time. A associação pode ter funcionado, mas não consigo te dar o link.")
                return

            team_data = team_response.json()
            team_name = team_data.get("name")
            thread_id = team_data.get("thread_id")

            if not thread_id:
                await query.edit_message_caption(f"✅ Você foi registrado no time '{team_name}', mas parece que não há um tópico associado a ele no Telegram.")
                return

            
            topic_link = urljoin(f"{GROUP_LINK}/", str(thread_id))
            keyboard = [[InlineKeyboardButton("➡️ Ir para o grupo!", url=topic_link)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if reg_response.status_code == 201:
                logger.info(f"User {user.username} successfully registered for team '{team_name}' (ID: {team_id}).")
                await query.edit_message_caption(
                    f"✅ Você foi registrado no time '{team_name}' com sucesso!",
                    reply_markup=reply_markup
                )
            elif reg_response.status_code == 400 and "already exists" in reg_response.text:
                logger.info(f"User {user.username} was already registered for a team.")
                await query.edit_message_caption(
                    f"Você já está em um time, mas aqui está o link para '{team_name}':",
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_caption(f"❌ Erro ao registrar no time: {reg_response.text}")

    except httpx.RequestError as e:
        logger.error(f"HTTP error while trying to register participant: {e}")
        await query.edit_message_caption("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        logger.error(f"An unexpected error occurred in join_team: {e}")
        await query.edit_message_caption("❌ Um erro inesperado aconteceu.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ação Cancelada. Use /start para ver o menu principal.")
    return ConversationHandler.END

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message.is_topic_message or not message.message_thread_id:
        return
    await message.reply_text(
        "❗ Para salvar localização, envie a imagem como **Arquivo/Documento** (não como foto). "
        "Toque no clipe > Arquivo > selecione a imagem original. Depois reenvie aqui."
    )
    return

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles document submissions (JPEG and HEIC images) from users within a group topic.
    """
    message = update.message
    user = update.effective_user

    if not message.is_topic_message or not message.message_thread_id:
        return

    document = message.document
    if document.mime_type not in ['image/jpeg', 'image/heic']:
        await message.reply_text("Please send JPEG images as files.")
        return

    doc_file = await context.bot.get_file(document.file_id)
    participant_id = str(user.id)

    await message.reply_text("Processing your image... ⏳")

    try:
        doc_bytes = await doc_file.download_as_bytearray()

        files = {'photo': (document.file_name, bytes(doc_bytes))}
        data = {
            'participant_id': participant_id,
            'thread_id': message.message_thread_id
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(f"{BACKEND_URL}/submissions/", files=files, data=data)

        if response.status_code == 201:
            logger.info(f"User {user.username} submitted an image in topic {message.message_thread_id}.")
            await message.reply_text("✅ Image sent successfully! Awaiting analysis.")
        elif response.status_code == 403:
            logger.warning(f"User {user.username} tried to submit to a wrong team in topic {message.message_thread_id}.")
            await message.reply_text("❌ You can only submit photos to the chat of the team you are registered in.")
        elif response.status_code == 404:
            if "Participant" in response.text:
                await message.reply_text("❌ You don't seem to be registered yet. Please use the bot's private chat menu to register.")
            else: # Team not found for the thread
                await message.reply_text("❌ This chat is not associated with any team.")
        else:
            await message.reply_text(f"❌ Error sending the image: {response.text}")

    except Exception as e:
        logger.error(f"Error handling document submission for user {user.id} in topic {message.message_thread_id}: {e}")
        await message.reply_text("❌ An unexpected error occurred. Please try again later.")

async def remove_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removes points from a team's score."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not message.is_topic_message or not message.message_thread_id:
        return

    try:
        chat_admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = {admin.user.id for admin in chat_admins}

        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem remover pontos.")
            return

        if not context.args:
            await update.message.reply_text("Por favor, especifique a quantidade de pontos a ser removida. Ex: /remove_points 20")
            return

        points_to_remove = int(context.args[0])
        thread_id = message.message_thread_id

        async with httpx.AsyncClient() as client:
            try:
                # First, get the team_id from the thread_id
                get_team_response = await client.get(f"{BACKEND_URL}/teams/by_thread/{thread_id}")
                
                if get_team_response.status_code != 200:
                    await update.message.reply_text(f"❌ Erro ao encontrar o time para este chat: {get_team_response.text}")
                    return

                team_id = get_team_response.json()["id"]

                # Now, remove the points from the team
                remove_score_response = await client.patch(
                    f"{BACKEND_URL}/teams/{team_id}/score",
                    json={"points": points_to_remove}
                )

                if remove_score_response.status_code == 200:
                    await update.message.reply_text(f"✅ {points_to_remove} pontos foram removidos do time.")
                else:
                    await update.message.reply_text(f"❌ Erro ao remover os pontos: {remove_score_response.text}")
            
            except httpx.RequestError as e:
                await update.message.reply_text(f"❌ Erro de comunicação com o servidor: {e}")
                logger.error(f"Failed to remove points due to communication error: {e}")

    except (IndexError, ValueError):
        await update.message.reply_text("Comando inválido. Use /remove_points <pontos>.")
    except Exception as e:
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")
        logger.error(f"Failed to remove points: {e}")



async def export_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Asks the user for the export format.
    """
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != 'supergroup':
        await update.message.reply_text("Este comando só pode ser usado em um chat de grupo.")
        return

    try:
        chat_admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = {admin.user.id for admin in chat_admins}

        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem exportar os dados.")
            return

        keyboard = [
            [InlineKeyboardButton("CSV", callback_data="export_csv")],
            [InlineKeyboardButton("JSON", callback_data="export_json")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Selecione o formato para exportar os dados:", reply_markup=reply_markup)

    except Exception as e:
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")
        logger.error(f"Failed to ask for export format: {e}")


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fetches the data from the backend and sends the file to the user.
    """
    query = update.callback_query
    await query.answer()

    format = query.data.split("_")[-1]

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{BACKEND_URL}/export/{format}")

            if response.status_code == 200:
                if format == "csv":
                    file_content = response.content
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=file_content,
                        filename="submissions.csv"
                    )
                elif format == "json":
                    file_content = response.content
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=file_content,
                        filename="submissions.json"
                    )
            else:
                await query.edit_message_text(f"❌ Erro ao exportar os dados: {response.text}")

    except Exception as e:
        await query.edit_message_text(f"❌ Um erro inesperado aconteceu: {e}")
        logger.error(f"Failed to export data: {e}")


async def create_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Creates a new event in the backend.
    This command can only be used by administrators in the group chat.
    """
    user = update.effective_user
    chat = update.effective_chat

    # Check if the command is used in a supergroup
    if chat.type != 'supergroup':
        await update.message.reply_text("Este comando só pode ser usado em um chat de grupo.")
        return

    try:
        # Get chat administrators
        chat_admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = {admin.user.id for admin in chat_admins}

        # Check if the user is an administrator
        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem criar novos eventos.")
            return

        async with httpx.AsyncClient() as client:
            # The date is sent in ISO 8601 format
            event_data = {"date": datetime.datetime.utcnow().isoformat()}
            response = await client.post(f"{BACKEND_URL}/events/", json=event_data)

            if response.status_code == 201:
                event_id = response.json()["id"]
                event_date = response.json()["date"]
                await update.message.reply_text(f"✅ Novo evento criado com sucesso! ID: {event_id}, Data: {event_date}")
                logger.info(f"New event created with ID {event_id} by admin {user.username}")
            else:
                await update.message.reply_text(f"❌ Erro ao criar o evento: {response.text}")
                logger.error(f"Failed to create event. Backend response: {response.text}")

    except httpx.RequestError as e:
        logger.error(f"HTTP error while creating event: {e}")
        await update.message.reply_text("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")
        logger.error(f"Failed to create event: {e}")


class RankingManager:
    def __init__(self, application):
        self.application = application
        self.ranking_thread_id = None
        self.last_ranking_message_id = None
        self.last_ranking_text = None

    async def setup(self):
        await self.create_ranking_topic()
        if self.ranking_thread_id:
            self.application.add_handler(MessageHandler(
                filters.ChatType.SUPERGROUP,
                self.delete_user_message
            ))
            scheduler = AsyncIOScheduler()
            scheduler.add_job(self.show_ranking, 'interval', minutes=1)
            scheduler.start()

    async def create_ranking_topic(self):
        try:
            new_topic = await self.application.bot.create_forum_topic(chat_id=CHAT_ID, name="Ranking")
            self.ranking_thread_id = new_topic.message_thread_id
            logger.info(f"Created 'Ranking' topic with thread_id {self.ranking_thread_id}")
        except Exception as e:
            if "topic with the same name already exists" in str(e):
                logger.warning("'Ranking' topic already exists. The bot will not post rankings until it is restarted.")
            else:
                logger.error(f"Failed to create 'Ranking' topic: {e}")

    async def delete_user_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.message_thread_id == self.ranking_thread_id:
            # Check if the user is an admin
            chat_admins = await self.application.bot.get_chat_administrators(CHAT_ID)
            admin_ids = {admin.user.id for admin in chat_admins}
            if update.message.from_user.id not in admin_ids:
                await update.message.delete()

    async def show_ranking(self):
        if not self.ranking_thread_id:
            logger.warning("Ranking thread_id not available. Skipping ranking update.")
            return

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{BACKEND_URL}/ranking/")

                if response.status_code == 200:
                    teams = response.json()
                    if not teams:
                        ranking_message = "Ainda não há pontuação no ranking."
                    else:
                        ranking_message = "🏆 **Ranking da Competição** 🏆\n\n"
                        for i, team in enumerate(teams):
                            ranking_message += f"{i+1}º - {team['name']}: {team['score']} pontos\n"

                    if self.last_ranking_text == ranking_message:
                        return

                    if self.last_ranking_message_id:
                        await self.application.bot.edit_message_text(
                            chat_id=CHAT_ID,
                            message_id=self.last_ranking_message_id,
                            text=ranking_message,
                            parse_mode="Markdown"
                        )
                    else:
                        message = await self.application.bot.send_message(
                            chat_id=CHAT_ID,
                            message_thread_id=self.ranking_thread_id,
                            text=ranking_message,
                            parse_mode="Markdown"
                        )
                        self.last_ranking_message_id = message.message_id
                    self.last_ranking_text = ranking_message
                else:
                    logger.error(f"Failed to fetch ranking. Backend response: {response.text}")

        except httpx.RequestError as e:
            logger.error(f"HTTP error while fetching ranking: {e}")
        except Exception as e:
            logger.error(f"Failed to show ranking: {e}")


def main() -> None:
    """Starts the bot and sets up handlers."""
    if not TELEGRAM_TOKEN or CHAT_ID == 0:
        logger.error("TELEGRAM_TOKEN or CHAT_ID not set correctly in .env")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Filters ---
    private_filter = filters.ChatType.PRIVATE
    group_topic_filter = filters.ChatType.SUPERGROUP & filters.IS_TOPIC_MESSAGE

    application.add_handler(CommandHandler("start", start, filters=private_filter))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    
    # New handler for the registration-based join flow
    application.add_handler(CallbackQueryHandler(join_team, pattern=r"^join_team_"))

    # Group handler for photo submissions in topics
    application.add_handler(MessageHandler(filters.PHOTO & group_topic_filter, handle_photo))
    application.add_handler(MessageHandler(filters.Document.IMAGE & group_topic_filter, handle_document))

    # Command to create a new event
    application.add_handler(CommandHandler("create_event", create_event_command, filters=filters.ChatType.SUPERGROUP))

    # Command to remove points from a team
    application.add_handler(CommandHandler("remove_points", remove_points, filters=filters.ChatType.SUPERGROUP))

    # Command to export data
    application.add_handler(CommandHandler("export", export_data_command, filters=filters.ChatType.SUPERGROUP))
    application.add_handler(CallbackQueryHandler(export_data, pattern="^export_csv$"))
    application.add_handler(CallbackQueryHandler(export_data, pattern="^export_json$"))

    # --- Start background tasks ---
    ranking_manager = RankingManager(application)
    loop = asyncio.get_event_loop()
    loop.create_task(ranking_manager.setup())

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()