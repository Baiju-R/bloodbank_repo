from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = "This command is deprecated because SMS-based donor matching was removed."

    def handle(self, *args, **options):
        raise CommandError(
            "The automated matching engine has been retired and this command is no longer available."
        )
