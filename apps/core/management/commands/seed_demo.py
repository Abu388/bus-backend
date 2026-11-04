from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.fares.models import Fare
from apps.fleet.models import PhysicalBus
from apps.routes.models import Fermata, Route, RouteDirection, RouteStop

User = get_user_model()


def get_model_fields(model_cls) -> set[str]:
    return {f.name for f in model_cls._meta.get_fields() if getattr(f, 'concrete', False)}


class Command(BaseCommand):
    help = "Seeds demo users, Addis Ababa fermatas, routes, fares, and buses. Safe to run multiple times."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding demo data...")

        # 1. Create Demo Users
        user_fields = get_model_fields(User)

        def upsert_user(username, email, phone, role, password="Password123!", is_staff=False, is_superuser=False):
            # Find any existing user matching phone, email, or username so re-seeding never collides
            query = Q()
            if "phone" in user_fields:
                query |= Q(phone=phone)
            if "phone_number" in user_fields:
                query |= Q(phone_number=phone)
            if "email" in user_fields:
                query |= Q(email=email)
            if "username" in user_fields:
                query |= Q(username=username)

            user = User.objects.filter(query).first()

            defaults = {
                "username": username,
                "email": email,
                "phone": phone,
                "phone_number": phone,
                "full_name": username.replace("_", " ").title(),
                "first_name": username.capitalize(),
                "last_name": "Demo",
                "role": role,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
            }

            if user:
                for k, v in defaults.items():
                    if k in user_fields:
                        setattr(user, k, v)
            else:
                user = User(**{k: v for k, v in defaults.items() if k in user_fields})

            user.set_password(password)
            user.save()
            return user

        admin_user = upsert_user(
            "admin_demo", "admin@bus.et", "+251911000001", "admin", is_staff=True, is_superuser=True
        )
        upsert_user("cashier1", "cashier1@bus.et", "+251911000002", "casher")
        upsert_user("cashier2", "cashier2@bus.et", "+251911000003", "casher")
        upsert_user("passenger1", "passenger1@bus.et", "+251911000004", "passenger")

        # 2. Create Addis Ababa Fermatas
        fermata_specs = [
            ("Mexico Square", 9.0104, 38.7445),
            ("Legehar", 9.0118, 38.7525),
            ("Meskel Square / Stadium", 9.0108, 38.7612),
            ("Kazanchis", 9.0155, 38.7720),
            ("Haya Hulet (22)", 9.0168, 38.7890),
            ("Megenagna", 9.0205, 38.8025),
        ]
        fermatas = []
        for name, lat, lon in fermata_specs:
            f, _ = Fermata.objects.update_or_create(
                name=name,
                defaults={"latitude": lat, "longitude": lon},
            )
            fermatas.append(f)

        # 3. Create Route 124 (Mexico - Megenagna)
        route_fields = get_model_fields(Route)
        route_defaults = {"name": "Mexico - Megenagna"}
        for start_key in ("first_fermata", "start_fermata", "origin"):
            if start_key in route_fields:
                route_defaults[start_key] = fermatas[0]
        for end_key in ("last_fermata", "end_fermata", "destination"):
            if end_key in route_fields:
                route_defaults[end_key] = fermatas[-1]
        if "status" in route_fields:
            route_defaults["status"] = "active"
        if "is_active" in route_fields:
            route_defaults["is_active"] = True

        route, _ = Route.objects.update_or_create(
            public_bus_number=124,
            defaults=route_defaults,
        )

        # 4. Link Ordered RouteStops
        rs_fields = get_model_fields(RouteStop)
        seq_field = (
            "sequence_number"
            if "sequence_number" in rs_fields
            else ("order" if "order" in rs_fields else "sequence")
        )
        for idx, fermata in enumerate(fermatas, start=1):
            RouteStop.objects.update_or_create(
                route=route,
                fermata=fermata,
                defaults={seq_field: idx},
            )

        # 5. Create Outbound & Inbound RouteDirections
        rd_fields = get_model_fields(RouteDirection)
        outbound_coords = [[float(f.longitude), float(f.latitude)] for f in fermatas]
        inbound_coords = list(reversed(outbound_coords))

        for direction_name, coords in [("outbound", outbound_coords), ("inbound", inbound_coords)]:
            rd_defaults = {}
            if "geometry" in rd_fields:
                rd_defaults["geometry"] = coords
            if "stop_distances" in rd_fields:
                rd_defaults["stop_distances"] = {}
            RouteDirection.objects.update_or_create(
                route=route,
                direction=direction_name,
                defaults=rd_defaults,
            )

        # 6. Create Active Fare (15.00 ETB) — automatically sets configured_by or created_by
        fare_fields = get_model_fields(Fare)
        if not Fare.objects.filter(route=route, effective_to__isnull=True).exists():
            fare_kwargs = {
                "route": route,
                "amount": Decimal("15.00"),
            }
            if "effective_from" in fare_fields:
                fare_kwargs["effective_from"] = timezone.now()
            for f in Fare._meta.concrete_fields:
                if isinstance(f, models.ForeignKey) and f.related_model == User:
                    fare_kwargs[f.name] = admin_user
            Fare.objects.create(**fare_kwargs)

        # 7. Create Two Physical Buses Sharing Route 124
        bus_fields = get_model_fields(PhysicalBus)
        for targa_code in ["3-AA-12401", "3-AA-12402"]:
            bus_defaults = {"route": route, "status": "active"}
            if "capacity" in bus_fields:
                bus_defaults["capacity"] = 65
            for f in PhysicalBus._meta.concrete_fields:
                if isinstance(f, models.ForeignKey) and f.related_model == User and not f.null:
                    bus_defaults[f.name] = admin_user
            PhysicalBus.objects.update_or_create(
                targa=targa_code,
                defaults=bus_defaults,
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Seed complete! Demo accounts created (Password: Password123!):\n"
                "  - Admin:     admin_demo / admin@bus.et / +251911000001 (role: admin)\n"
                "  - Cashier 1: cashier1   / cashier1@bus.et / +251911000002 (role: casher)\n"
                "  - Cashier 2: cashier2   / cashier2@bus.et / +251911000003 (role: casher)\n"
                "  - Passenger: passenger1 / passenger1@bus.et / +251911000004 (role: passenger)"
            )
        )