import os

from django.core.management.base import BaseCommand, CommandError

try:
    from pyngrok import conf, ngrok
except ImportError as exc:  # pragma: no cover - defensive guard
    raise CommandError(
        "pyngrok is required for this command. Install dependencies via 'pip install -r requirements.txt'."
    ) from exc


class Command(BaseCommand):
    help = "Launches an Ngrok tunnel for the local Django server and optionally updates the env file."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8000, help="Local port to expose (default: 8000).")
        parser.add_argument(
            "--region",
            default=os.environ.get("NGROK_REGION", "us"),
            help="Ngrok region code (default: env NGROK_REGION or 'us').",
        )
        parser.add_argument(
            "--authtoken",
            default=os.environ.get("NGROK_AUTHTOKEN"),
            help="Ngrok auth token. Falls back to env NGROK_AUTHTOKEN or the saved config.",
        )
        parser.add_argument(
            "--no-inspect",
            action="store_true",
            help="Disable Ngrok's inspection interface (enabled by default).",
        )

    def handle(self, *args, **options):
        port = options["port"]
        region = options["region"]
        authtoken = options["authtoken"]
        inspect = not options["no_inspect"]

        if authtoken:
            self.stdout.write("Using provided Ngrok auth token…")
            conf.get_default().auth_token = authtoken
        elif not conf.get_default().auth_token:
            self.stdout.write(
                self.style.WARNING(
                    "No Ngrok auth token detected. Free tunnels will expire quickly; set NGROK_AUTHTOKEN or pass --authtoken."
                )
            )

        self.stdout.write(f"Opening Ngrok tunnel on port {port} (region={region})…")
        tunnel = ngrok.connect(addr=port, proto="http", region=region, bind_tls=True, inspect=inspect)
        public_url = tunnel.public_url

        self.stdout.write(self.style.SUCCESS(f"Tunnel ready: {public_url}"))
        self.stdout.write("Keep this command running while sharing the project.")

        try:
            self.stdout.write("Press Ctrl+C to close the tunnel and release the URL.")
            ngrok_process = ngrok.get_ngrok_process()
            ngrok_process.proc.wait()
        except KeyboardInterrupt:
            self.stdout.write("Shutting down tunnel…")
            ngrok.disconnect(public_url)
            ngrok.kill()
