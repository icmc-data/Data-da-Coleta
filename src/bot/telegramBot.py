import os
import logging
import json
import datetime
from dotenv import load_dotenv
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# --- Configuration ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", 0))
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

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
    # Define the shared keyboard and caption
    keyboard = [
        [InlineKeyboardButton("🚀 Crie um grupo novo", callback_data="create_topic_start")],
        [InlineKeyboardButton("➡️ Entre em um grupo já criado", callback_data="join_topic_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption_text = "Bem vindo! Para iniciar, clique em uma das opções abaixo."

    # Check if the update is a callback query (from the list existing groups button)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        # Edit the existing message
        await query.edit_message_caption(
            caption=caption_text,
            reply_markup=reply_markup
        )

    # message (from the /start command)
    else:
        # Send a new photo message
        await update.message.reply_photo(
            photo=open(os.path.join(PATH_IMAGES, 'hamster.jpg'), 'rb'),
            caption=caption_text,
            reply_markup=reply_markup
        )

async def ask_for_topic_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Edits the photo message to ask for the topic name."""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("⬅️ Ou volte pro menu principal", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_caption(
        caption="✍️ Qual nome você gostaria de dar para o seu grupo? Digite no chat!",
        reply_markup=reply_markup 
    )

    return ASK_TOPIC_NAME

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

async def show_join_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edits the photo message to show a list of teams that can be joined."""
    query = update.callback_query
    await query.answer()
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{BACKEND_URL}/teams/")
            if response.status_code != 200:
                await query.edit_message_caption(caption="❌ Erro ao carregar os times do servidor.")
                return
            
            teams = response.json()
            
            # Filter for teams that have a thread_id, meaning they are associated with a Telegram topic
            joinable_teams = [team for team in teams if team.get("thread_id")]

            if not joinable_teams:
                keyboard = [[InlineKeyboardButton("⬅️ Voltar pro menu inicial", callback_data="main_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_caption(
                    caption="Nenhum grupo foi criado ainda 😭😭 Porque não criar o seu próprio?",
                    reply_markup=reply_markup
                )
                return

            keyboard = []
            for team in sorted(joinable_teams, key=lambda t: t['name']):
                button = InlineKeyboardButton(f"➡️ {team['name']}", callback_data=f"join_team_{team['id']}")
                keyboard.append([button])
            
            keyboard.append([InlineKeyboardButton("⬅️ Voltar pro menu inicial", callback_data="main_menu")])
                
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_caption(
                caption="Selecione um dos grupos abaixo para se registrar e participar ➡️", 
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"An error occurred in show_join_menu: {e}")
        await query.edit_message_caption(caption="❌ Um erro inesperado aconteceu ao carregar os times.")

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
                await query.edit_message_text("❌ Erro ao obter detalhes do time. A associação pode ter funcionado, mas não consigo te dar o link.")
                return

            team_data = team_response.json()
            team_name = team_data.get("name")
            thread_id = team_data.get("thread_id")

            if not thread_id:
                await query.edit_message_text(f"✅ Você foi registrado no time '{team_name}', mas parece que não há um tópico associado a ele no Telegram.")
                return

            link_chat_id = str(CHAT_ID).replace("-100", "")
            topic_link = f"https://t.me/c/{link_chat_id}/{thread_id}"
            keyboard = [[InlineKeyboardButton("➡️ Ir para o grupo!", url=topic_link)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if reg_response.status_code == 201:
                logger.info(f"User {user.username} successfully registered for team '{team_name}' (ID: {team_id}).")
                await query.edit_message_text(
                    f"✅ Você foi registrado no time '{team_name}' com sucesso!",
                    reply_markup=reply_markup
                )
            elif reg_response.status_code == 400 and "already exists" in reg_response.text:
                logger.info(f"User {user.username} was already registered for a team.")
                await query.edit_message_text(
                    f"Você já está em um time, mas aqui está o link para '{team_name}':",
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(f"❌ Erro ao registrar no time: {reg_response.text}")

    except httpx.RequestError as e:
        logger.error(f"HTTP error while trying to register participant: {e}")
        await query.edit_message_text("❌ Erro de comunicação com o servidor. Tente novamente mais tarde.")
    except Exception as e:
        logger.error(f"An unexpected error occurred in join_team: {e}")
        await query.edit_message_text("❌ Um erro inesperado aconteceu.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ação Cancelada. Use /start para ver o menu principal.")
    return ConversationHandler.END

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles photo submissions from users within a group topic.
    It assumes the user is already a registered participant of the team associated with the topic.
    """
    # The message is in a topic, so we can reply in the topic.
    message = update.message
    user = update.effective_user
    
    # Check if the message is in a topic
    if not message.is_topic_message or not message.message_thread_id:
        return # Should not happen due to the filter, but as a safeguard

    photo_file = await message.photo[-1].get_file()
    participant_id = str(user.id)

    # Let the user know the photo is being processed
    await message.reply_text("Processando sua foto... ⏳")

    try:
        photo_bytes = await photo_file.download_as_bytearray()

        files = {'photo': (photo_file.file_path.split('/')[-1], bytes(photo_bytes))}
        data = {'participant_id': participant_id}

        async with httpx.AsyncClient() as client:
            response = await client.post(f"{BACKEND_URL}/submissions/", files=files, data=data)

        if response.status_code == 201:
            await message.reply_text("✅ Foto enviada com sucesso! Aguardando análise.")
        elif response.status_code == 404 and "Participant" in response.text:
             await message.reply_text("❌ Você não parece estar registrado neste time. Por favor, use o menu do bot em uma conversa privada para se registrar.")
        else:
            await message.reply_text(f"❌ Erro ao enviar a foto: {response.text}")

    except Exception as e:
        logger.error(f"Error handling photo submission for user {user.id} in topic {message.message_thread_id}: {e}")
        await message.reply_text("❌ Ocorreu um erro inesperado. Tente novamente mais tarde.")

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


def main() -> None:
    """Starts the bot and sets up handlers."""
    if not TELEGRAM_TOKEN or CHAT_ID == 0:
        logger.error("TELEGRAM_TOKEN or CHAT_ID not set correctly in .env")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- Filters ---
    private_filter = filters.ChatType.PRIVATE
    group_topic_filter = filters.ChatType.SUPERGROUP & filters.IS_TOPIC_MESSAGE

    # --- Conversation Handler for creating topics (DM-only) ---
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_for_topic_name, pattern="^create_topic_start$")],
        states={
            ASK_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, create_topic)],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)],
    )

    # --- Handlers ---
    # DM handlers for starting and creating groups
    application.add_handler(CommandHandler("start", start, filters=private_filter))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(show_join_menu, pattern="^join_topic_menu$"))
    
    # New handler for the registration-based join flow
    application.add_handler(CallbackQueryHandler(join_team, pattern=r"^join_team_"))

    # Group handler for photo submissions in topics
    application.add_handler(MessageHandler(filters.PHOTO & group_topic_filter, handle_photo))

    # Command to create a new event
    application.add_handler(CommandHandler("create_event", create_event_command, filters=filters.ChatType.SUPERGROUP))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()