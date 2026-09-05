"""Terminal emulation (pyte) so we can see what is actually on screen."""
import pyte


class Screen:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)

    def feed(self, data):
        try:
            self.stream.feed(data)
        except Exception:
            pass

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        try:
            self.screen.resize(rows, cols)
        except Exception:
            pass

    def text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def reset(self):
        self.screen.reset()
