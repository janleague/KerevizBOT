import discord
from discord.ext import commands

class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help", help="Show all available commands.")
    async def help_command(self, ctx):
        embed = discord.Embed(
            title="📘 Kereviz Bot Help Menu",
            description="Use the commands below to interact with the bot.",
            color=discord.Color.green()
        )

        hypixel_cmds = []
        general_cmds = []
        admin_cmds = []

        for command in self.bot.commands:
            if command.hidden:
                continue
            category = getattr(command, "category", "General")
            entry = f"`!{command.name}` - {command.help or 'No description'}"
            if category == "Hypixel":
                hypixel_cmds.append(entry)
            elif category == "Admin":
                admin_cmds.append(entry)
            else:
                general_cmds.append(entry)

        if hypixel_cmds:
            embed.add_field(name="🧱 Hypixel Commands", value="\n".join(hypixel_cmds), inline=False)
        if general_cmds:
            embed.add_field(name="✨ General Commands", value="\n".join(general_cmds), inline=False)
        if admin_cmds:
            embed.add_field(name="🔒 Admin Commands", value="\n".join(admin_cmds), inline=False)

        embed.set_footer(text="Made with ❤️ by Kereviz")
        embed.set_thumbnail(url="https://media.discordapp.net/attachments/1229049517790466178/1229049663500324905/Kerevizzz.png?ex=67edd9f2&is=67ec8872&hm=088ce555c2393189085e4de29d77183fb998100b0b7f21600b479fada5a1c5f6&=&format=webp&quality=lossless")  # Optional logo

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(HelpCommand(bot))
