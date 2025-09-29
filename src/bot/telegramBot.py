import os
import logging
import json
from dotenv import load_dotenv
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
from ultralytics import YOLO

# --- Configuration ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", 0))
TOPICS_DB_FILE = "topics.json"

# --- Logging ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Conversation States ---
ASK_TOPIC_NAME = range(1)

def load_topics() -> dict:
    try:
        with open(TOPICS_DB_FILE, "r") as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_topic(thread_id: int, topic_name: str):
    topics = load_topics()
    topics[str(thread_id)] = topic_name
    with open(TOPICS_DB_FILE, "w") as f: json.dump(topics, f, indent=4)

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
    """Creates a topic, posts a welcome message in it, and replies in the DM."""
    topic_name = update.message.text
    user = update.effective_user 
    
    try:
        # Create the new topic in the configured group
        new_topic = await context.bot.create_forum_topic(chat_id=CHAT_ID, name=topic_name)
        thread_id = new_topic.message_thread_id
        save_topic(thread_id, topic_name)
        
        welcome_message_text = (
            f"👋 **Seja bem-vindo ao grupo \"{topic_name}\"!**\n\n"
            f"O grupo foi criado pelo usuário: {user.mention_markdown()}. "
        )
        await context.bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=thread_id,
            text=welcome_message_text,
            parse_mode="Markdown"
        )

        # Send the confirmation with a join button back to the user in the DM
        link_chat_id = str(CHAT_ID).replace("-100", "")
        topic_link = f"https://t.me/c/{link_chat_id}/{thread_id}"
        
        keyboard = [[InlineKeyboardButton("➡️ Ir para o grupo!", url=topic_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Maravilha! O grupo \"{topic_name}\" foi criado com sucesso.", reply_markup=reply_markup
        )
        logger.info(f"User {user.username} created topic '{topic_name}' in chat {CHAT_ID}")

    except Exception as e:
        await update.message.reply_text(f"❌ Um erro aconteceu :( : {e}")
        logger.error(f"Failed  {CHAT_ID}: {e}")
        
    return ConversationHandler.END

async def show_join_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edits the photo message to show a list of topic join links with a back button."""
    query = update.callback_query
    await query.answer()
    
    topics = load_topics()
    
    if not topics:
        keyboard = [[InlineKeyboardButton("⬅️ Voltar pro menu inicial", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_caption(
            caption="Nenhum grupo foi criado ainda 😭😭 Porque não criar o seu próprio?", # New caption
            reply_markup=reply_markup
        )
        return

    link_chat_id = str(CHAT_ID).replace("-100", "")
    keyboard = []
    for thread_id, name in sorted(topics.items(), key=lambda item: item[1]):
        topic_link = f"https://t.me/c/{link_chat_id}/{thread_id}"
        button = InlineKeyboardButton(name, url=topic_link)
        keyboard.append([button])
    
    keyboard.append([InlineKeyboardButton("⬅️ Voltar pro menu inicial", callback_data="main_menu")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_caption(
        caption="Selecione um dos grupos abaixo para participar dele ➡️", 
        reply_markup=reply_markup
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ação Cancelada. Use /start para ver o menu principal.")
    return ConversationHandler.END

def main() -> None:
    """Starts the DM-only bot."""
    if not TELEGRAM_TOKEN or CHAT_ID == 0:
        logger.error("TELEGRAM_TOKEN ou CHAT_ID não foram setadas corretamente no .env")
        return
        
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    private_filter = filters.ChatType.PRIVATE

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_for_topic_name, pattern="^create_topic_start$")],
        states={
            ASK_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, create_topic)],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)],
    )

    application.add_handler(CommandHandler("start", start, filters=private_filter))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(show_join_menu, pattern="^join_topic_menu$"))

    logger.info("DM-Only Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()