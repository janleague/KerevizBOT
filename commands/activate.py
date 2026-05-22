import discord
from discord.ext import commands

class ToggleCommand(commands.Cog):
    """Owner‑only runtime toggle for any command.
    Usage: `!a <command>`
    ‑ Disables the target command until the owner toggles it again.
    ‑ Re‑enables if it was already disabled.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.disabled: set[str] = set()

    # Owner‑only guard
    @commands.is_owner()
    @commands.command(name="a", usage="!a <command>", help="Enable/disable a command.")
    async def toggle(self, ctx: commands.Context, command_name: str):
        cmd = self.bot.get_command(command_name)
        if not cmd:
            await ctx.send(f"❌ Command `{command_name}` not found.")
            return

        if command_name in self.disabled:
            cmd.enabled = True
            self.disabled.remove(command_name)
            await ctx.send(f"✅ Command `{command_name}` has been **enabled**.")
        else:
            cmd.enabled = False
            self.disabled.add(command_name)
            await ctx.send(f"🚫 Command `{command_name}` has been **disabled** until re‑enabled.")

async def setup(bot: commands.Bot):
    cog = ToggleCommand(bot)
    # show under Admin in help
    cog.toggle.category = "Admin"
    await bot.add_cog(cog)
