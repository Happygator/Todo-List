import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import pytz
import random
from dotenv import load_dotenv
import database

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
# TARGET_USER_ID removed, no longer used globally

intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)

class TodoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.last_reminder_dates = {} # {user_id: date}
        self.user_cache = {} # {user_id: UserObj}

    async def setup_hook(self):
        await database.init_db()
        await database.migrate_to_multi_user() # Run migration
        await database.fix_date_formats()
        await self.tree.sync()
        self.daily_reminder.start()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        
        # 1. Initialize settings for all users who have ever created a task
        existing_users = await database.get_all_unique_users_from_tasks()
        count_init = 0
        for u_id in existing_users:
            await database.ensure_user_initialized(u_id)
            count_init += 1
        print(f"Ensured settings for {count_init} users.")

        # 2. Cache users for display purposes (optimization)
        # We can cache everyone who has settings now (which is everyone with tasks)
        users = await database.get_users_with_settings()
        for user_id in users:
             try:
                user = await self.fetch_user(user_id)
                self.user_cache[user_id] = user # cache it
             except Exception as e:
                print(f"Failed to cache user {user_id}: {e}")

    async def on_disconnect(self):
        print("WARNING: Bot disconnected! This often happens if the token is being used elsewhere.")
        
    async def on_resumed(self):
        print("INFO: Bot resumed session.")

    async def send_daily_summary(self, user_id, user_obj, is_startup=False):
        # Get timezone 
        tz_str = await database.get_setting(user_id, 'timezone')
        if not tz_str:
            return # No timezone, no reminder

        try:
            timezone = pytz.timezone(tz_str)
            now = datetime.datetime.now(timezone)
            today_str = now.date().isoformat()
            
            # Rollover past tasks
            await database.rollover_past_tasks(user_id, today_str)
            
            tasks = await database.get_tasks_for_reminders(user_id, today_str)
            
            msg_tasks = []
            prefix = "I am online! " if is_startup else "Daily Reminder! "

            if tasks:
                header = f"**{prefix}Here are the tasks due today:**\n"
                msg_tasks = tasks
            else:
                # Fallback: Check for any upcoming tasks (limit 5)
                # This ensures users with tasks (even if not due today) still get a reminder
                upcoming = await database.get_top_tasks(user_id, limit=5)
                if upcoming:
                    header = f"**{prefix}No tasks due today. Here are your upcoming tasks:**\n"
                    msg_tasks = upcoming
                else:
                    # No tasks at all -> Silent
                    return

            msg = header
            for task in msg_tasks:
                msg += await self.format_task_display(task)
            await user_obj.send(msg)

        except Exception as e:
            print(f"Error sending summary to {user_id}: {e}")

    def parse_date(self, date_str):
        """Helper to parse date string (YYYY-MM-DD or days offset) into YYYY-MM-DD string."""
        if not date_str:
            return None
            
        final_date_str = None
        if date_str.isdigit():
            days = int(date_str)
            target_date = (datetime.date.today() + datetime.timedelta(days=days))
            final_date_str = target_date.isoformat()
        else:
            try:
                dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                final_date_str = dt.date().isoformat()
            except ValueError:
                return None # parsing failed
        
        # Enforce rule: No past dates.
        if final_date_str:
            today_iso = datetime.date.today().isoformat()
            if final_date_str < today_iso:
                final_date_str = today_iso
                
        return final_date_str

    def format_task_date(self, date_str):
        if not date_str:
            return "No due date"
        
        try:
            due_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            today = datetime.date.today()
            delta = (due_date - today).days
            
            if delta == 0:
                return "Today"
            elif delta == 1:
                return "Tomorrow"
            elif delta == 2:
                return "In 2 days"
            elif delta < 0:
                return f"Overdue ({date_str})"
            else:
                return f"Due: {date_str}"
        except ValueError:
            return date_str

    async def format_task_display(self, task):
        date_display = self.format_task_date(task['due_date'])
        
        assigner_str = ""
        # Check if assigned by someone else
        if task['assigner_id'] != task['user_id']:
            assigner_id = task['assigner_id']
            assigner_name = f"User {assigner_id}"
            
            # Check cache
            if assigner_id in self.user_cache:
                assigner = self.user_cache[assigner_id]
                assigner_name = assigner.display_name
            else:
                try:
                    # Try to get from cache first, then fetch
                    assigner = self.get_user(assigner_id)
                    if not assigner:
                        assigner = await self.fetch_user(assigner_id)
                    
                    if assigner:
                        self.user_cache[assigner_id] = assigner
                        assigner_name = assigner.display_name
                except Exception as e:
                    print(f"Error fetching user {assigner_id}: {e}")
            
            assigner_str = f" (from {assigner_name})"

        return f"- [ID: {task['id']}] {task['name']} ({date_display}){assigner_str}\n"

    @tasks.loop(minutes=1)
    async def daily_reminder(self):
        # iterate over all users with settings (implies they might want reminders)
        user_ids = await database.get_users_with_settings()
        
        for user_id in user_ids:
            tz_str = await database.get_setting(user_id, 'timezone')
            if not tz_str:
                continue
            
            try:
                timezone = pytz.timezone(tz_str)
            except pytz.UnknownTimeZoneError:
                continue

            now = datetime.datetime.now(timezone)
            
            # Check user's preferred time (default 08:00)
            target_time_str = await database.get_setting(user_id, 'reminder_time') or "08:00"
            try:
                target_hour, target_minute = map(int, target_time_str.split(':'))
            except ValueError:
                target_hour, target_minute = 8, 0

            if now.hour == target_hour and now.minute == target_minute:
                # Check stored date for this user
                last_date = self.last_reminder_dates.get(user_id)
                if last_date == now.date():
                    continue 

                user = await self.fetch_user(user_id)
                if user:
                    await self.send_daily_summary(user_id, user, is_startup=False)
                
                self.last_reminder_dates[user_id] = now.date()

    @daily_reminder.before_loop
    async def before_daily_reminder(self):
        await self.wait_until_ready()

class GiveTaskView(discord.ui.View):
    def __init__(self, target_user_id, requester_user_id, task_name, task_date_str, bot_instance):
        super().__init__(timeout=86400) # 24 hour timeout
        self.target_user_id = target_user_id
        self.requester_user_id = requester_user_id
        self.task_name = task_name
        self.task_date_str = task_date_str
        self.bot = bot_instance

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("This confirmation is not for you!", ephemeral=True)
            return False
        return True

    async def _notify_requester(self, message):
        try:
            requester = self.bot.get_user(self.requester_user_id)
            if not requester:
                requester = await self.bot.fetch_user(self.requester_user_id)
            
            if requester:
                await requester.send(message)
        except Exception as e:
            print(f"Failed to notify requester {self.requester_user_id}: {e}")

    async def on_timeout(self):
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        # Determine the message to edit on the target user's side
        # Access the message attached to the view if possible. 
        # In discord.py views, self.message might be set if sent via message.
        # But for interactions, we might need to be careful.
        # However, for wait_for/timeout, we can try to edit if we have the message reference.
        # Since this view is sent via DM using user.send(view=view), the message object is returned.
        # BUT we didn't store it in __init__ or assignment. 
        # Actually, when sent via channel.send(), the view is attached.
        # Limitations: We can't edit the message easily without the message object. 
        # Let's hope the user clicked something? No, this is timeout.
        
        # Notification to requester
        await self._notify_requester(f"⚠️ Task request **{self.task_name}** to <@{self.target_user_id}> timed out (no response).")

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Pass requester_user_id as assigner_id
        task_id = await database.add_task(self.target_user_id, self.task_name, self.task_date_str, assigner_id=self.requester_user_id)
        date_display = self.bot.format_task_date(self.task_date_str) if self.task_date_str else ""
        
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        await interaction.edit_original_response(
            content=f"✅ Task accepted and added! **{self.task_name}** ({date_display}) (ID: {task_id})", 
            view=self
        )
        
        # Notify requester
        await self._notify_requester(f"✅ <@{self.target_user_id}> accepted your task: **{self.task_name}**")
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Disable buttons
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(
            content=f"❌ Task declined: **{self.task_name}**", 
            view=self
        )
        
        # Notify requester
        await self._notify_requester(f"❌ <@{self.target_user_id}> declined your task: **{self.task_name}**")
        self.stop()

bot = TodoBot()

@bot.tree.command(name="givetask", description="Assign a task to another user (requires their confirmation)")
@app_commands.describe(user="The user to assign the task to", name="The task name", date="Due date (YYYY-MM-DD) OR days from now")
async def givetask(interaction: discord.Interaction, user: discord.User, name: str, date: str = None):
    await interaction.response.defer(ephemeral=True)
    # Parse date
    final_date_str = None
    if date:
        final_date_str = bot.parse_date(date)
        if final_date_str is None:
             await interaction.followup.send("Invalid date format. Please use YYYY-MM-DD or a number of days.")
             return

    # Create view
    view = GiveTaskView(
        target_user_id=user.id,
        requester_user_id=interaction.user.id,
        task_name=name,
        task_date_str=final_date_str,
        bot_instance=bot
    )
    
    date_display = bot.format_task_date(final_date_str) if final_date_str else "No due date"
    msg = f"**{interaction.user.display_name}** wants to assign you a task:\n**{name}**\nDue: {date_display}\n\nDo you accept?"
    
    try:
        await user.send(msg, view=view)
        await interaction.followup.send(f"Task request sent to {user.mention} via DM.")
    except discord.Forbidden:
        await interaction.followup.send(f"Could not DM {user.mention}. They might have DMs blocked.")
    except Exception as e:
        await interaction.followup.send(f"Failed to send DM: {e}")

@bot.tree.command(name="add", description="Add a new task")
@app_commands.describe(name="The task name", date="Due date (YYYY-MM-DD) OR days from now (e.g. 1 for tomorrow)")
async def add(interaction: discord.Interaction, name: str, date: str = None):
    await interaction.response.defer()
    final_date_str = None
    if date:
        final_date_str = bot.parse_date(date)
        if final_date_str is None:
             await interaction.followup.send("Invalid date format. Please use YYYY-MM-DD or a number of days.")
             return

             await interaction.followup.send("Invalid date format. Please use YYYY-MM-DD or a number of days.")
             return

    # Ensure user is initialized (has timezone/reminder time)
    await database.ensure_user_initialized(interaction.user.id)

    # Pass assigner_id=interaction.user.id (explicitly self-assigned)
    task_id = await database.add_task(interaction.user.id, name, final_date_str, assigner_id=interaction.user.id)
    date_display = bot.format_task_date(final_date_str) if final_date_str else ""
    await interaction.followup.send(f"Task added: **{name}** ({date_display}) (ID: {task_id})")

@bot.tree.command(name="complete", description="Mark task(s) as complete (removes them)")
@app_commands.describe(task_ids_str="The ID(s) of the tasks to complete, separated by commas (e.g. 1,5,7)")
async def complete(interaction: discord.Interaction, task_ids_str: str):
    await interaction.response.defer()
    # Parse IDs
    try:
        task_ids = [int(id_str.strip()) for id_str in task_ids_str.split(',') if id_str.strip().isdigit()]
    except ValueError:
        await interaction.followup.send("Invalid format. Please use numbers separated by commas (e.g. 1,5,7).")
        return

    if not task_ids:
         await interaction.followup.send("No valid task IDs found.")
         return

    deleted_count = await database.delete_tasks(interaction.user.id, task_ids)
    
    if deleted_count > 0:
        task_s = "tasks" if deleted_count > 1 else "task"
        await interaction.followup.send(f"Marked {deleted_count} {task_s} as complete.")
    else:
        await interaction.followup.send(f"No tasks found with those IDs (or they didn't belong to you).")

@bot.tree.command(name="tasks", description="Show 5 upcoming tasks")
async def tasks_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    tasks = await database.get_top_tasks(interaction.user.id, limit=5)
    if not tasks:
        await interaction.followup.send("No upcoming tasks found.")
        return

    msg = "**Upcoming Tasks:**\n"
    for task in tasks:
        msg += await bot.format_task_display(task)
    
    await interaction.followup.send(msg)

@bot.tree.command(name="alltasks", description="Show all tasks")
async def alltasks(interaction: discord.Interaction):
    await interaction.response.defer()
    tasks = await database.get_all_undone_tasks_sorted(interaction.user.id)
    if not tasks:
        await interaction.followup.send("No tasks found.")
        return

    msg_header = "**All Tasks:**\n"
    current_msg = msg_header
    
    for task in tasks:
        line = await bot.format_task_display(task)
        
        if len(current_msg) + len(line) > 1900: 
            if current_msg == msg_header:
                 await interaction.followup.send(current_msg)
            else:
                 await interaction.followup.send(current_msg)
            current_msg = line
        else:
            current_msg += line

    if current_msg:
        # Since we always defer, is_done is always true, so always followup
        await interaction.followup.send(current_msg)

@bot.tree.command(name="gettask", description="Get a random task to focus on")
async def gettask(interaction: discord.Interaction):
    await interaction.response.defer()
    all_tasks = await database.get_all_undone_tasks_sorted(interaction.user.id)
    if not all_tasks:
        await interaction.followup.send("No tasks available! Good job.")
        return

    today = datetime.date.today()
    priority_tasks = []
    future_buckets = {}
    no_date_tasks = []

    for task in all_tasks:
        if task['due_date']:
            due = datetime.datetime.strptime(task['due_date'], "%Y-%m-%d").date()
            if due <= today:
                priority_tasks.append(task)
            else:
                if due not in future_buckets:
                    future_buckets[due] = []
                future_buckets[due].append(task)
        else:
            no_date_tasks.append(task)

    target_task = None
    reason = ""
    
    if priority_tasks:
        target_task = random.choice(priority_tasks)
        reason = "due today (or overdue)"
    else:
        sorted_dates = sorted(future_buckets.keys())
        if sorted_dates:
             next_date = sorted_dates[0]
             target_task = random.choice(future_buckets[next_date])
             reason = f"due on {next_date}"
        elif no_date_tasks:
            target_task = random.choice(no_date_tasks)
            reason = "from your backlog"
    
    if target_task:
         await interaction.followup.send(f"**Focus Task:** [ID: {target_task['id']}] {target_task['name']} ({reason})")
    else:
         await interaction.followup.send("No task found.")

@bot.tree.command(name="timezone", description="Set the timezone for daily reminders")
@app_commands.describe(tz="Timezone code (e.g., PST, EST, US/Pacific)")
async def timezone_cmd(interaction: discord.Interaction, tz: str):
    tz_map = {
        'PST': 'US/Pacific',
        'EST': 'US/Eastern',
        'CST': 'US/Central',
        'MST': 'US/Mountain',
        'GMT': 'Etc/GMT',
        'UTC': 'UTC'
    }
    
    target_tz = tz_map.get(tz.upper(), tz)
    
    try:
        pytz.timezone(target_tz)
    except pytz.UnknownTimeZoneError:
        await interaction.response.send_message(f"Invalid timezone: {tz}. Try standard names like 'US/Pacific' or 3-letter codes.", ephemeral=True)
        return

    await database.set_setting(interaction.user.id, 'timezone', target_tz)
    await interaction.response.send_message(f"Timezone set to: {target_tz}")

@bot.tree.command(name="reminder", description="Set or view your daily reminder time")
@app_commands.describe(time="Optional: Time in 24h format (HH:MM). Leave empty to check current settings.")
async def reminder_cmd(interaction: discord.Interaction, time: str = None):
    # Always fetch timezone for display
    tz_str = await database.get_setting(interaction.user.id, 'timezone') or "Not Set"

    if time is None:
        # View mode
        current_time = await database.get_setting(interaction.user.id, 'reminder_time') or "08:00"
        await interaction.response.send_message(f"Your daily reminder is set for **{current_time}** (Timezone: {tz_str}).", ephemeral=True)
        return

    # Set mode - Validate format
    try:
        hour, minute = map(int, time.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await interaction.response.send_message("Invalid time format. Please use HH:MM (24-hour), e.g., 08:00 or 17:30.", ephemeral=True)
        return

    # Store normalized string
    normalized_time = f"{hour:02d}:{minute:02d}"
    await database.set_setting(interaction.user.id, 'reminder_time', normalized_time)
    
    await interaction.response.send_message(f"Daily reminder set for **{normalized_time}** (Timezone: {tz_str}).")

import socket
import sys

# ... (existing imports)


# ... (imports)

def check_single_instance():
    """Ensure only one instance, and return socket for shutdown listener."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Try to bind
        sock.bind(("127.0.0.1", 60001))
        sock.listen(1) # Listen for shutdown signal
        sock.setblocking(False) # Non-blocking for asyncio
    except socket.error:
        print("Error: Another instance of the bot is already running.")
        sys.exit(1)
    
    return sock

async def shutdown_listener(sock):
    """Background task to listen for shutdown signals on the socket."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Accept connection
            client, _ = await loop.sock_accept(sock)
            
            # Read data
            data = await loop.sock_recv(client, 1024)
            if data == b"SHUTDOWN":
                print("Received shutdown signal. Closing bot...")
                client.close()
                await bot.close()
                break
            client.close()
        except:
             # Ignore errors to keep listener alive
             pass

if __name__ == "__main__":
    lock_socket = check_single_instance()
    
    # We need to inject the listener into the bot's loop once it starts
    # setup_hook is a good place, but we need to pass lock_socket to the bot instance
    # OR we can just add it to the loop in setup_hook if we make lock_socket global or attached to bot.
    bot.lock_socket = lock_socket 
    
    # Monkey patch setup_hook to add the listener task? 
    # Or just subclass properly? We already have TodoBot class.
    # Let's attach the task in TodoBot.setup_hook
    
    old_setup = bot.setup_hook
    
    async def new_setup():
        await old_setup()
        bot.loop.create_task(shutdown_listener(bot.lock_socket))
        
    bot.setup_hook = new_setup
    
    try:
        bot.run(TOKEN)
    finally:
        lock_socket.close()
