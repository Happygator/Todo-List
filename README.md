# Discord To-Do List Bot

A feature-rich Discord bot to manage your personal to-do list directly within Discord. Supports multiple users, daily reminders, and automatic task management.

## Features

-   **Individual Task Lists**: Every user has their own private list of tasks.
-   **Daily Reminders**: Get a direct message every day at 8:00 AM (in your time zone) with your tasks for the day.
-   **Smart Scheduling**:
    -   Tasks due in the past are automatically rolled over to "Today".
    -   Relative date support (e.g., "1" for tomorrow).
-   **Sorting**: Tasks are always sorted by due date (Earliest -> Latest -> No Date).

## Installation

1.  **Prerequisites**:
    -   Python 3.8+
    -   A Discord Bot Token (from the [Discord Developer Portal](https://discord.com/developers/applications))

2.  **Setup**:
    Clone the repository and install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configuration**:
    Create a `.env` file in the project root:
    ```env
    DISCORD_TOKEN=your_token_here
    ```

4.  **Run**:
    ```bash
    python bot.py
    ```

## Commands

### Task Management
| Command | Description | Example |
| :--- | :--- | :--- |
| `/add <name> [date]` | Add a new task. Date can be `YYYY-MM-DD` or a number of days from now (e.g. `1` for tomorrow). | `/add Buy milk 1` |
| `/complete <ids>` | Mark task(s) as complete. Separate IDs with commas (e.g. `1,2,5`). | `/complete 1,5` |
| `/tasks` | Show the top 5 tasks due soonest. Overdue tasks appear first. | `/tasks` |
| `/alltasks` | Show **every** task in your list, sorted by date. | `/alltasks` |
| `/gettask` | Get a single random task to focus on. Prioritizes tasks due today/overdue. | `/gettask` |
| `/givetask <user> <name> [date]` | Assign a task to another user. They must confirm it via buttons. | `/givetask @User Clean kitchen 1` |

### Settings
| Command | Description | Example |
| :--- | :--- | :--- |
| `/timezone <tz>` | Set your timezone for the 8:00 AM daily reminder. | `/timezone PST` or `/timezone US/Eastern` |

## Daily Reminders

To receive daily reminders:
1.  Set your timezone using `/timezone`.
2.  The bot will DM you every day at 8:00 AM in your timezone with a list of tasks due that day.
3.  If you have no tasks due, but have upcoming tasks, it will show the upcoming ones instead.
