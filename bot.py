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
        
        users = await database.get_users_with_settings()
        for user_id in users:
             try:
                user = await self.fetch_user(user_id)
                self.user_cache[user_id] = user # cache it
                # Startup message removed per user request
                # if user:
                #    await self.send_daily_summary(user_id, user, is_startup=True)
             except Exception as e:
                print(f"Failed to cache user {user_id}: {e}")

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
                    msg += await self.format_task_display(task)
                await user_obj.send(msg)
            else:
                 await user_obj.send(header)

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

class GiveTaskView(discord.ui.View):
    def __init__(self, target_user_id, requester_user_id, task_name, task_date_str, bot_instance):
        super().__init__(timeout=300) # 5 minute timeout
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

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Pass requester_user_id as assigner_id
        task_id = await database.add_task(self.target_user_id, self.task_name, self.task_date_str, assigner_id=self.requester_user_id)
        date_display = self.bot.format_task_date(self.task_date_str) if self.task_date_str else ""
        
        # Disable buttons
        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"✅ Task accepted and added! **{self.task_name}** ({date_display}) (ID: {task_id})", 
            view=self
        )
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
        self.stop()

bot = TodoBot()

@bot.tree.command(name="givetask", description="Assign a task to another user (requires their confirmation)")
@app_commands.describe(user="The user to assign the task to", name="The task name", date="Due date (YYYY-MM-DD) OR days from now")
async def givetask(interaction: discord.Interaction, user: discord.User, name: str, date: str = None):
    # Parse date
    final_date_str = None
    if date:
        final_date_str = bot.parse_date(date)
        if final_date_str is None:
             await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD or a number of days.", ephemeral=True)
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
        await interaction.response.send_message(f"Task request sent to {user.mention} via DM.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"Could not DM {user.mention}. They might have DMs blocked.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to send DM: {e}", ephemeral=True)

@bot.tree.command(name="add", description="Add a new task")
@app_commands.describe(name="The task name", date="Due date (YYYY-MM-DD) OR days from now (e.g. 1 for tomorrow)")
async def add(interaction: discord.Interaction, name: str, date: str = None):
    final_date_str = None
    if date:
        final_date_str = bot.parse_date(date)
        if final_date_str is None:
             await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD or a number of days.", ephemeral=True)
             return

    # Pass assigner_id=interaction.user.id (explicitly self-assigned)
    task_id = await database.add_task(interaction.user.id, name, final_date_str, assigner_id=interaction.user.id)
    date_display = bot.format_task_date(final_date_str) if final_date_str else ""
    await interaction.response.send_message(f"Task added: **{name}** ({date_display}) (ID: {task_id})")

@bot.tree.command(name="complete", description="Mark task(s) as complete (removes them)")
@app_commands.describe(task_ids_str="The ID(s) of the tasks to complete, separated by commas (e.g. 1,5,7)")
async def complete(interaction: discord.Interaction, task_ids_str: str):
    # Parse IDs
    try:
        task_ids = [int(id_str.strip()) for id_str in task_ids_str.split(',') if id_str.strip().isdigit()]
    except ValueError:
        await interaction.response.send_message("Invalid format. Please use numbers separated by commas (e.g. 1,5,7).", ephemeral=True)
        return

    if not task_ids:
         await interaction.response.send_message("No valid task IDs found.", ephemeral=True)
         return

    deleted_count = await database.delete_tasks(interaction.user.id, task_ids)
    
    if deleted_count > 0:
        task_s = "tasks" if deleted_count > 1 else "task"
        await interaction.response.send_message(f"Marked {deleted_count} {task_s} as complete.")
    else:
        await interaction.response.send_message(f"No tasks found with those IDs (or they didn't belong to you).", ephemeral=True)

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
