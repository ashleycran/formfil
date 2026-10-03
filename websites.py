from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database
import keyboards
import re

async def websites_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    websites = await database.get_websites(user_id)
    
    text = f"🌐 My Websites\n\nTotal: {len(websites)}"
    await query.edit_message_text(text, reply_markup=keyboards.get_websites_keyboard())

async def websites_add_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['state'] = 'WAITING_FOR_WEBSITES'
    
    text = (
        "🌐 Add Websites\n\n"
        "Send one or more URLs (one per line or space-separated).\n\n"
        "Example:\n"
        "https://example.com\n"
        "https://another.com"
    )
    await query.edit_message_text(text, reply_markup=keyboards.get_cancel_keyboard("cancel_websites_add"))

async def websites_upload_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['state'] = 'WAITING_FOR_WEBSITES_FILE'
    
    text = "📄 Upload TXT\n\nSend a .txt file with one URL per line."
    await query.edit_message_text(text, reply_markup=keyboards.get_cancel_keyboard("cancel_websites_upload"))

async def websites_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    websites = await database.get_websites(user_id)
    
    if not websites:
        await query.edit_message_text("No websites added yet.", reply_markup=keyboards.get_back_home_keyboard("menu_websites"))
        return
        
    text = "🌐 Your Websites\n\n"
    for i, w in enumerate(websites[:50]): # limit for telegram msg length
        text += f"{i+1}. {w['url']}\n"
        
    if len(websites) > 50:
        text += f"\n... and {len(websites)-50} more."
        
    await query.edit_message_text(text, reply_markup=keyboards.get_back_home_keyboard("menu_websites"), disable_web_page_preview=True)

async def websites_remove_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    websites = await database.get_websites(user_id)
    
    if not websites:
        await query.edit_message_text("No websites to remove.", reply_markup=keyboards.get_back_home_keyboard("menu_websites"))
        return
        
    text = "🗑️ Select a website to remove:"
    keyboard = []
    
    for w in websites[:20]: # Pagination could be better here, keeping it simple for now
        keyboard.append([InlineKeyboardButton(w['url'], callback_data=f"websites_delprompt_{w['id']}")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_websites")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def websites_delprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    website_id = int(query.data.split('_')[2])
    
    text = f"Remove this website?\n\n[ID: {website_id}]"
    keyboard = [
        [InlineKeyboardButton("🗑️ Remove", callback_data=f"websites_dodelete_{website_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="websites_remove")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def websites_dodelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    website_id = int(query.data.split('_')[2])
    user_id = update.effective_user.id
    
    await database.remove_website(user_id, website_id=website_id)
    
    text = "✅ Website removed."
    await query.edit_message_text(text, reply_markup=keyboards.get_back_home_keyboard("menu_websites"))

def extract_urls(text):
    # Basic URL extraction
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
    valid_urls = []
    for url in urls:
        if 'localhost' in url or '127.0.0.1' in url or '0.0.0.0' in url or url.startswith('http://192.168.') or url.startswith('http://10.'):
            continue
        valid_urls.append(url)
    return valid_urls

async def handle_websites_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    
    if state != 'WAITING_FOR_WEBSITES':
        return False
        
    user_id = update.effective_user.id
    text = update.message.text
    
    urls = extract_urls(text)
    if not urls:
        await update.message.reply_text(
            "⚠️ No valid URLs found. Make sure they start with http:// or https://\n\nTry again or cancel below.",
            reply_markup=keyboards.get_cancel_keyboard("cancel_websites_add"),
        )
        return True
        
    added = 0
    for url in urls:
        if await database.add_website(user_id, url):
            added += 1
            
    context.user_data['state'] = None
    await update.message.reply_text(
        f"✅ Added {added} website(s). (Duplicates ignored)",
        reply_markup=keyboards.get_back_home_keyboard("menu_websites"),
    )
    return True

async def handle_websites_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    
    if state != 'WAITING_FOR_WEBSITES_FILE':
        return False
        
    user_id = update.effective_user.id
    document = update.message.document
    
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text(
            "⚠️ Please upload a .txt file. Try again or cancel below.",
            reply_markup=keyboards.get_cancel_keyboard("cancel_websites_upload"),
        )
        return True
        
    file = await context.bot.get_file(document.file_id)
    byte_array = await file.download_as_bytearray()
    content = byte_array.decode('utf-8')
    
    urls = extract_urls(content)
    added = 0
    duplicates = 0
    
    for url in urls:
        if await database.add_website(user_id, url):
            added += 1
        else:
            duplicates += 1
            
    context.user_data['state'] = None
    text = f"📄 Import Complete\n\nFound: {len(urls)}\nAdded: {added}\nDuplicates: {duplicates}"
    await update.message.reply_text(text, reply_markup=keyboards.get_back_home_keyboard("menu_websites"))
    return True
