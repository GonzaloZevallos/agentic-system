"""Verifica que el Agent SDK autentique con la suscripcion, sin API key."""
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage


async def main() -> None:
    opciones = ClaudeAgentOptions(
        system_prompt="Responde en una sola linea, sin preambulo.",
        allowed_tools=[],  # sin herramientas: es solo una prueba de auth
        max_turns=1,
        model="claude-sonnet-5",
    )
    async for mensaje in query(prompt="Decime OK y el modelo que sos.", options=opciones):
        if isinstance(mensaje, AssistantMessage):
            for bloque in mensaje.content:
                if isinstance(bloque, TextBlock):
                    print("respuesta:", bloque.text.strip())
        elif isinstance(mensaje, ResultMessage):
            print("costo_usd:", mensaje.total_cost_usd, "| turnos:", mensaje.num_turns)


anyio.run(main)
