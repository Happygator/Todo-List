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

    async def setup_hook(self):
        await database.init_db()
        await database.migrate_to_multi_user() # Run migration
        await database.fix_date_formats()
        await self.tree.sync()
        self.daily_reminder.start()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        # We can't easily DM *everyone* on startup without iterating. 
        # But we can try to DM users we know about (migration target).
        # For now, let's skip the "online" DM broadcast to avoid mass spam if bot restarts,
        # or just implement it for users we find in settings.
        
        users = await database.get_users_with_settings()
        for user_id in users:
             try:
                user = await self.fetch_user(user_id)
                if user:
                    await self.send_daily_summary(user_id, user, is_startup=True)
             except Exception as e:
                print(f"Failed to send startup message to {user_id}: {e}")

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
            
            prefix = "I am online! " if is_startup else "Daily Reminder! "
            header = f"**{prefix}Here are the tasks due today:**\n"
            
            # Fallback (only for startup or if explicit check needed, for reminder we usually only want to send if tasks exist? 
            # Or user wants to know they are free. 
            # Requirement: "direct message ... with the current list of tasks that are due today."
            # If empty, maybe say so?)
            
            # For startup, user requested behavior: "if there are no tasks for today, show the 5 most imminent tasks"
            if not tasks:
                 upcoming = await database.get_top_tasks(user_id, limit=5)
                 if upcoming:
                     header = f"**{prefix}No tasks due today. Here are your upcoming tasks:**\n"
                     tasks = upcoming
                 else:
                     if is_startup:
                         header = f"**{prefix}No tasks found at all.**"
                     else:
                         # For daily reminder, if absolutely nothing, maybe say "No tasks due today!"
                         header = f"**{prefix}No tasks due today!**"

            if tasks:
                msg = header
                for task in tasks:
                    date_display = self.format_task_date(task['due_date'])
                    msg += f"- [ID: {task['id']}] {task['name']} ({date_display})\n"
                await user_obj.send(msg)
            else:
                 await user_obj.send(header)

        except Exception as e:
            print(f"Error sending summary to {user_id}: {e}")

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
            
            if now.hour == 8 and now.minute == 0:
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

bot = TodoBot()

@bot.tree.command(name="add", description="Add a new task")
@app_commands.describe(name="The task name", date="Due date (YYYY-MM-DD) OR days from now (e.g. 1 for tomorrow)")
async def add(interaction: discord.Interaction, name: str, date: str = None):
    # Validate date
    final_date_str = None
    if date:
        if date.isdigit():
            days = int(date)
            target_date = (datetime.date.today() + datetime.timedelta(days=days))
            final_date_str = target_date.isoformat()
        else:
            try:
                dt = datetime.datetime.strptime(date, "%Y-%m-%d")
                final_date_str = dt.date().isoformat()
            except ValueError:
                await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD or a number of days.", ephemeral=True)
                return
    
    # Enforce rule: No past dates.
    today_iso = datetime.date.today().isoformat()
    if final_date_str and final_date_str < today_iso:
        final_date_str = today_iso

    task_id = await database.add_task(interaction.user.id, name, final_date_str)
    date_display = bot.format_task_date(final_date_str) if final_date_str else ""
    await interaction.response.send_message(f"Task added: **{name}** ({date_display}) (ID: {task_id})")

@bot.tree.command(name="complete", description="Mark a task as complete (removes it)")
@app_commands.describe(task_id="The ID of the task to complete")
async def complete(interaction: discord.Interaction, task_id: int):
    success = await database.delete_task(interaction.user.id, task_id)
    if success:
        await interaction.response.send_message(f"Task {task_id} completed and removed.")
    else:
        await interaction.response.send_message(f"Task with ID {task_id} not found (or not yours).", ephemeral=True)

@bot.tree.command(name="tasks", description="Show 5 upcoming tasks")
async def tasks_cmd(interaction: discord.Interaction):
    tasks = await database.get_top_tasks(interaction.user.id, limit=5)
    if not tasks:
        await interaction.response.send_message("No upcoming tasks found.")
        return

    msg = "**Upcoming Tasks:**\n"
    for task in tasks:
        date_display = bot.format_task_date(task['due_date'])
        msg += f"- [ID: {task['id']}] {task['name']} ({date_display})\n"
    
    await interaction.response.send_message(msg)

@bot.tree.command(name="alltasks", description="Show all tasks")
async def alltasks(interaction: discord.Interaction):
    tasks = await database.get_all_undone_tasks_sorted(interaction.user.id)
    if not tasks:
        await interaction.response.send_message("No tasks found.")
        return

    msg_header = "**All Tasks:**\n"
    current_msg = msg_header
    
    for task in tasks:
        date_display = bot.format_task_date(task['due_date'])
        line = f"- [ID: {task['id']}] {task['name']} ({date_display})\n"
        
        if len(current_msg) + len(line) > 1900: 
            if current_msg == msg_header:
                 await interaction.response.send_message(current_msg)
            else:
                 await interaction.followup.send(current_msg)
            current_msg = line
        else:
            current_msg += line

    if current_msg:
        if current_msg == msg_header:
             await interaction.response.send_message(current_msg)
        else:
             if interaction.response.is_done():
                 await interaction.followup.send(current_msg)
             else:
                 await interaction.response.send_message(current_msg)

@bot.tree.command(name="gettask", description="Get a random task to focus on")
async def gettask(interaction: discord.Interaction):
    all_tasks = await database.get_all_undone_tasks_sorted(interaction.user.id)
    if not all_tasks:
        await interaction.response.send_message("No tasks available! Good job.")
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
         await interaction.response.send_message(f"**Focus Task:** [ID: {target_task['id']}] {target_task['name']} ({reason})")
    else:
         await interaction.response.send_message("No task found.")

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

if __name__ == "__main__":
    bot.run(TOKEN)
