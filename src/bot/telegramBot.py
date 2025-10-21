import os
import logging
import json
import datetime
import time
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
from decorators import rate_limit_handler

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
                caption="DATA 🤝 SEMCOMP \n\nSelecione abaixo sua casa do overflow para se registrar e participar do Data Da Coleta", 
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
                    f"✅ Você está pronto para representar sua casa {team_name}! Junte amigos e boa aventura!",
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

@rate_limit_handler
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message.is_topic_message or not message.message_thread_id:
        return
    await message.reply_text(
        "❗ Para salvar localização, envie a imagem como **Arquivo/Documento** (não como foto). "
        "Toque no clipe > Arquivo > selecione a imagem original. Depois reenvie aqui."
    )
    return

@rate_limit_handler
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
            await message.reply_text("✅ Imagem submetida com sucesso! Aguarde uns segundinhos até a imagem ser processada.")
        elif response.status_code == 403:
            logger.warning(f"User {user.username} tried to submit to a wrong team in topic {message.message_thread_id}.")
            await message.reply_text("❌ Ô zé, vc tá mandando a foto pra casa errada.")
        elif response.status_code == 404:
            if "Participant" in response.text:
                await message.reply_text("❌ Vc parece não tá registrado ainda. Manda mensagem pro nosso mano @DataDaColeta_Bot pra se registrar.")
            else: # Team not found for the thread
                await message.reply_text("❌ Esse canal não é pra mandar fotos 😭😭😭😭😭.")
        else:
            await message.reply_text(f"❌ Deu um erro aí. Perdoar 🙏🙏. Erro: {response.text}")

    except Exception as e:
        logger.error(f"Error handling document submission for user {user.id} in topic {message.message_thread_id}: {e}")
        await message.reply_text("❌ Algum trem quebrou aqui :( Tenta de novo mais tarde, pufavo.")

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
            await update.message.reply_text("❌ Seu safadinho. Apenas administradores podem remover pontos 😤😤")
            return

        if not context.args:
            await update.message.reply_text("Esqueceu, zé? Precisa botar a quantidade de pontos depois do comando Ex: /remove_points 20")
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
        self.thread_id_file = "src/bot/ranking_thread_id.txt"

    async def setup(self):
        self.load_ranking_thread_id()
        if not self.ranking_thread_id:
            await self.create_ranking_topic()

        if self.ranking_thread_id:
            self.application.add_handler(MessageHandler(
                filters.ChatType.SUPERGROUP,
                self.delete_user_message
            ))
            scheduler = AsyncIOScheduler()
            scheduler.add_job(self.show_ranking, 'interval', minutes=1)
            scheduler.start()

    def load_ranking_thread_id(self):
        try:
            with open(self.thread_id_file, "r") as f:
                self.ranking_thread_id = int(f.read().strip())
                logger.info(f"Loaded 'Ranking' topic thread_id {self.ranking_thread_id} from file.")
        except (FileNotFoundError, ValueError):
            logger.info(f"'{self.thread_id_file}' not found or invalid. A new topic will be created.")
            self.ranking_thread_id = None

    def save_ranking_thread_id(self):
        with open(self.thread_id_file, "w") as f:
            f.write(str(self.ranking_thread_id))
        logger.info(f"Saved 'Ranking' topic thread_id {self.ranking_thread_id} to file.")

    async def create_ranking_topic(self):
        try:
            new_topic = await self.application.bot.create_forum_topic(chat_id=CHAT_ID, name="Ranking")
            self.ranking_thread_id = new_topic.message_thread_id
            self.save_ranking_thread_id()
            logger.info(f"Created 'Ranking' topic with thread_id {self.ranking_thread_id}")
        except Exception as e:
            if "topic with the same name already exists" in str(e):
                logger.warning("'Ranking' topic already exists, but I could not get its thread_id. "
                               f"Please find the thread_id of the 'Ranking' topic and save it to the '{self.thread_id_file}' file. "
                               "The bot will not post rankings until this is done.")
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

async def create_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the conversation to create a new topic.
    This command can only be used in a private chat with the bot.
    """
    user = update.effective_user
    try:
        # Get chat administrators of the supergroup
        chat_admins = await context.bot.get_chat_administrators(CHAT_ID)
        admin_ids = {admin.user.id for admin in chat_admins}

        # Check if the user is an administrator
        if user.id not in admin_ids:
            await update.message.reply_text("❌ Apenas administradores podem criar novos times.")
            return ConversationHandler.END

        await update.message.reply_text("Qual o nome do novo time que você quer criar?")
        return ASK_TOPIC_NAME

    except Exception as e:
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")
        logger.error(f"Failed to start create topic conversation: {e}")
        return ConversationHandler.END

async def create_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Creates a new team in the backend, a new topic in Telegram,
    and registers the creator as the first participant.
    """
    topic_name = update.message.text
    user = update.effective_user
    
    try:
        # Step 1: Get the latest event
        async with httpx.AsyncClient() as client:
            events_response = await client.get(f"{BACKEND_URL}/events/")
            if events_response.status_code != 200 or not events_response.json():
                await update.message.reply_text("❌ Não há eventos ativos para criar times. Peça para um admin criar um novo evento.")
                return ConversationHandler.END

            latest_event_id = events_response.json()[0]["id"]

            # Step 2: Create the team in the backend
            team_data = {"name": topic_name, "event_id": latest_event_id}
            response = await client.post(f"{BACKEND_URL}/teams/", json=team_data)

            if response.status_code == 201:
                team_id = response.json()["id"]
                logger.info(f"Team '{topic_name}' created in backend with ID {team_id}.")

                # Step 3: Register the creator as a participant
                participant_data = {
                    "id": str(user.id),
                    "name": user.full_name,
                    "team_id": team_id
                }
                part_response = await client.post(f"{BACKEND_URL}/participants/", json=participant_data)
                if part_response.status_code != 201:
                    logger.error(f"Failed to register participant {user.username} for new team '{topic_name}'. Backend response: {part_response.text}")

            elif response.status_code == 400 and "already exists" in response.text:
                await update.message.reply_text(f"❌ O time '{topic_name}' já existe. Por favor, escolha outro nome.")
                return ConversationHandler.END
            else:
                await update.message.reply_text(f"❌ Erro ao criar o time no servidor: {response.text}")
                return ConversationHandler.END

        # Step 4: Create the topic in Telegram
        new_topic = await context.bot.create_forum_topic(chat_id=CHAT_ID, name=topic_name)
        thread_id = new_topic.message_thread_id

        # Step 5: Update the team in the backend with the thread_id
        async with httpx.AsyncClient() as client:
            update_data = {"thread_id": str(thread_id)}
            update_response = await client.patch(f"{BACKEND_URL}/teams/{team_id}", json=update_data)
            if update_response.status_code != 200:
                logger.error(f"Failed to update team {team_id} with thread_id {thread_id}. Backend response: {update_response.text}")

        welcome_message_text = (
            f'👋 **Seja bem-vindo ao grupo "{topic_name}"!**\n\n'
            f"O grupo foi criado e você foi registrado como o primeiro participante, {user.mention_markdown()}."
        )
        await context.bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=thread_id,
            text=welcome_message_text,
            parse_mode="Markdown"
        )

        # Step 6: Send confirmation to the user
        link_chat_id = str(CHAT_ID).replace("-100", "")
        topic_link = f"https://t.me/c/{link_chat_id}/{thread_id}"
        
        keyboard = [[InlineKeyboardButton("➡️ Ir para o grupo!", url=topic_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Maravilha! O grupo \"{topic_name}\" foi criado e você foi registrado nele.", reply_markup=reply_markup
        )
        logger.info(f"User {user.username} created topic '{topic_name}' and was registered in chat {CHAT_ID}")

    except httpx.RequestError as e:
        logger.error(f"HTTP error while creating topic/participant: {e}")
        await update.message.reply_text("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        await update.message.reply_text(f"❌ Um erro inesperado aconteceu: {e}")
        logger.error(f"Failed to create topic and register participant in chat {CHAT_ID}: {e}")
        
    return ConversationHandler.END

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

    # Conversation handler for creating a new topic
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('create_topic', create_topic_command, filters=private_filter)],
        states={
            ASK_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_topic)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(conv_handler)

    # --- Start background tasks ---
    ranking_manager = RankingManager(application)
    loop = asyncio.get_event_loop()
    loop.create_task(ranking_manager.setup())

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()