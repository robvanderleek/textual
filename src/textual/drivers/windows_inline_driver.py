from __future__ import annotations

import asyncio
import shutil
import sys
from typing import TYPE_CHECKING

from textual import events
from textual.drivers import win32
from textual.drivers._writer_thread import WriterThread
from textual.drivers.windows_driver import WindowsDriver
from textual.geometry import Size

if TYPE_CHECKING:
    from textual import messages
    from textual.app import App


class WindowsInlineDriver(WindowsDriver):
    """Powers display and input for inline apps on Windows."""

    def __init__(
        self,
        app: App,
        *,
        debug: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
    ) -> None:
        """Initialize Windows inline driver.

        Args:
            app: The App instance.
            debug: Enable debug mode.
            mouse: Enable mouse support.
            size: Initial size of the terminal or `None` to detect.
        """
        super().__init__(app, debug=debug, mouse=mouse, size=size)
        self._file = sys.__stderr__

    @property
    def is_inline(self) -> bool:
        return True

    @property
    def can_suspend(self) -> bool:
        """Can this driver be suspended?"""
        return False

    def _get_terminal_size(self) -> tuple[int, int]:
        """Detect the size of the visible console window.

        Returns:
            The size of the terminal as a tuple of (WIDTH, HEIGHT).
        """
        try:
            width, height = shutil.get_terminal_size()
        except (AttributeError, ValueError, OSError):
            width, height = 80, 25
        return width or 80, height or 25

    def process_message(self, message: messages.Message) -> None:
        """Perform additional processing on a message, prior to sending.

        Args:
            message: A message to process.
        """
        # NOTE: This runs in a thread.
        if isinstance(message, events.CursorPosition):
            self.cursor_origin = (message.x, message.y)
        else:
            super().process_message(message)

    def start_application_mode(self) -> None:
        """Start application mode."""
        loop = asyncio.get_running_loop()

        def send_size_event(clear: bool = False) -> None:
            """Send the resize event, optionally clearing the screen.

            Args:
                clear: Clear the screen.
            """
            width, height = self._get_terminal_size()
            textual_size = Size(width, height)
            event = events.Resize(textual_size, textual_size)

            async def update_size() -> None:
                """Update the screen size."""
                if clear:
                    self.write("\x1b[2J")
                await self._app._post_message(event)

            asyncio.run_coroutine_threadsafe(update_size(), loop=loop)

        self._restore_console = win32.enable_application_mode(output=self._file)

        self._writer_thread = WriterThread(self._file)
        self._writer_thread.start()

        self.write("\x1b[?25l")  # Hide cursor
        self.write("\033[?1004h")  # Enable FocusIn/FocusOut.
        self.write("\x1b[>1u")  # https://sw.kovidgoyal.net/kitty/keyboard-protocol/
        self.flush()
        self._enable_mouse_support()
        self.write("\n" * self._app.INLINE_PADDING)
        self.flush()

        send_size_event()

        # The console buffer size reported in resize events includes the
        # scrollback, so query the size of the visible window instead.
        self._event_thread = win32.EventMonitor(
            loop,
            self._app,
            self.exit_event,
            self.process_message,
            on_resize=lambda: send_size_event(clear=True),
        )
        self._event_thread.start()
        self._enable_bracketed_paste()

    def stop_application_mode(self) -> None:
        """Stop application mode, restore state."""
        self._disable_bracketed_paste()
        self.disable_input()
        self.write("\x1b[<u")  # Disable kitty protocol
        self.write("\x1b[J")  # Clear down
        self.write("\x1b[?25h")  # Show cursor
        self.write("\033[?1004l")  # Disable FocusIn/FocusOut.
        self.flush()
