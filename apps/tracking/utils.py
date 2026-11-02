import math
from apps.routes.models import Fermata, RouteDirection, RouteStop


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculates the real-world distance in meters between two GPS coordinates (lat/lon).
    """
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def calculate_progress_and_next_stop(trip, bus_lat: float, bus_lon: float):
    """
    Given an active trip and the bus's current (lat, lon):
    1. Snaps the bus to the closest point on the RouteDirection's stored polyline.
    2. Calculates how many meters along the route the bus has traveled (distance_along_m).
    3. Finds the first upcoming Fermata ahead of the bus (next_fermata).
    """
    route_dir = RouteDirection.objects.filter(
        route=trip.route, direction=trip.direction
    ).first()

    # Load stops in sequence order (reverse order if the bus is driving 'inbound')
    stops = list(
        RouteStop.objects.filter(route=trip.route)
        .select_related('fermata')
        .order_by('sequence_number')
    )
    if trip.direction == 'inbound':
        stops.reverse()

    if not stops:
        return 0.0, None

    # Case 1: If OSRM polyline geometry is stored (list of [lon, lat] pairs)
    if route_dir and isinstance(route_dir.geometry, list) and len(route_dir.geometry) >= 2:
        coords = route_dir.geometry
        cumulative_dist = 0.0
        best_dist_along = 0.0
        min_dist_to_bus = float('inf')

        # Walk along the polyline points to find the point closest to the bus
        for i, point in enumerate(coords):
            pt_lon, pt_lat = point[0], point[1]
            if i > 0:
                prev_lon, prev_lat = coords[i - 1][0], coords[i - 1][1]
                cumulative_dist += haversine_m(prev_lat, prev_lon, pt_lat, pt_lon)

            dist_to_bus = haversine_m(bus_lat, bus_lon, pt_lat, pt_lon)
            if dist_to_bus < min_dist_to_bus:
                min_dist_to_bus = dist_to_bus
                best_dist_along = cumulative_dist

        # Find the first stop whose distance along the route is ahead of best_dist_along
        stop_distances = route_dir.stop_distances or {}
        for stop in stops:
            stop_dist = stop_distances.get(str(stop.fermata_id))
            if stop_dist is not None and float(stop_dist) > best_dist_along + 20.0:
                return round(best_dist_along, 1), stop.fermata

    # Case 2 (Fallback if geometry/stop_distances aren't generated yet):
    # Measure cumulative straight-line distance from stop to stop
    cumulative_stop_dist = 0.0
    closest_stop_index = 0
    min_stop_dist = float('inf')

    for idx, stop in enumerate(stops):
        d = haversine_m(bus_lat, bus_lon, stop.fermata.latitude, stop.fermata.longitude)
        if d < min_stop_dist:
            min_stop_dist = d
            closest_stop_index = idx

    for i in range(1, closest_stop_index + 1):
        prev_f = stops[i - 1].fermata
        curr_f = stops[i].fermata
        cumulative_stop_dist += haversine_m(
            prev_f.latitude, prev_f.longitude, curr_f.latitude, curr_f.longitude
        )

    # Next stop is the one immediately after the closest stop
    next_idx = min(closest_stop_index + 1, len(stops) - 1)
    return round(cumulative_stop_dist, 1), stops[next_idx].fermata