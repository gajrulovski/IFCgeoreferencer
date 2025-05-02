import ifcopenshell
import math
import argparse
from typing import Tuple, Optional, Dict, Any, List
import sys
import numpy as np

# Add PyProj for proper coordinate transformations
try:
    import pyproj
    from pyproj import Transformer, CRS
    PYPROJ_AVAILABLE = True
except ImportError:
    PYPROJ_AVAILABLE = False
    print("WARNING: PyProj not available. Install it for accurate coordinate transformations:")
    print("pip install pyproj")


def get_site_placement(ifc_file):
    """Extract the site's current placement data."""
    sites = ifc_file.by_type("IfcSite")
    if not sites:
        return None
    
    site = sites[0]
    return site.ObjectPlacement


def get_site_geo_data(ifc_file) -> Dict[str, Any]:
    """
    Extracts the site's geographic coordinates and map conversion data if available.
    
    Returns a dictionary with geographic coordinates and map conversion data.
    """
    sites = ifc_file.by_type("IfcSite")
    if not sites:
        return None
    
    site = sites[0]
    result = {
        "latitude": None,
        "longitude": None,
        "elevation": None,
        "raw_latitude": None,
        "raw_longitude": None,
        "map_conversion": None,
        "projected_crs": None,
        "epsg_code": None
    }
    
    # Get geographic coordinates
    if hasattr(site, "RefLatitude") and hasattr(site, "RefLongitude"):
        if site.RefLatitude:
            lat = site.RefLatitude.as_list() if hasattr(site.RefLatitude, 'as_list') else site.RefLatitude
            result["raw_latitude"] = lat
            result["latitude"] = lat[0] + lat[1]/60 + lat[2]/3600 + lat[3]/3600000000
        
        if site.RefLongitude:
            lon = site.RefLongitude.as_list() if hasattr(site.RefLongitude, 'as_list') else site.RefLongitude
            result["raw_longitude"] = lon
            result["longitude"] = lon[0] + lon[1]/60 + lon[2]/3600 + lon[3]/3600000000
        
        if hasattr(site, "RefElevation"):
            result["elevation"] = site.RefElevation
    
    # Try to get map conversion and projected CRS data (IFC4+ feature)
    contexts = ifc_file.by_type("IfcGeometricRepresentationContext")
    for context in contexts:
        if hasattr(context, "HasCoordinateOperation") and context.HasCoordinateOperation:
            for op in context.HasCoordinateOperation:
                if op.is_a("IfcMapConversion"):
                    result["map_conversion"] = {
                        "eastings": op.Eastings,
                        "northings": op.Northings,
                        "orthogonal_height": op.OrthogonalHeight if hasattr(op, "OrthogonalHeight") else None,
                        "x_axis_abscissa": op.XAxisAbscissa if hasattr(op, "XAxisAbscissa") else None,
                        "x_axis_ordinate": op.XAxisOrdinate if hasattr(op, "XAxisOrdinate") else None,
                        "scale": op.Scale if hasattr(op, "Scale") else None
                    }
                    
                    # Try to get the ProjectedCRS
                    if hasattr(op, "TargetCRS"):
                        target_crs = op.TargetCRS
                        result["projected_crs"] = {
                            "name": target_crs.Name if hasattr(target_crs, "Name") else None,
                            "description": target_crs.Description if hasattr(target_crs, "Description") else None,
                            "geodetic_datum": target_crs.GeodeticDatum if hasattr(target_crs, "GeodeticDatum") else None,
                            "vertical_datum": target_crs.VerticalDatum if hasattr(target_crs, "VerticalDatum") else None,
                            "map_projection": target_crs.MapProjection if hasattr(target_crs, "MapProjection") else None,
                            "map_zone": target_crs.MapZone if hasattr(target_crs, "MapZone") else None,
                            "map_unit": target_crs.MapUnit.Name if hasattr(target_crs, "MapUnit") and target_crs.MapUnit and hasattr(target_crs.MapUnit, "Name") else None
                        }
                        
                        # Try to extract EPSG code from name or ID if available
                        if result["projected_crs"]["name"] and "EPSG" in result["projected_crs"]["name"]:
                            # Try to extract EPSG code from the name
                            import re
                            epsg_match = re.search(r'EPSG:(\d+)', result["projected_crs"]["name"])
                            if epsg_match:
                                result["epsg_code"] = int(epsg_match.group(1))

    return result


def decimal_to_dms(decimal_degrees: float) -> Tuple[int, int, int, int]:
    """Convert decimal degrees to IFC format (degrees, minutes, seconds, millionths)"""
    degrees = int(decimal_degrees)
    minutes_float = abs(decimal_degrees - degrees) * 60
    minutes = int(minutes_float)
    seconds_float = (minutes_float - minutes) * 60
    seconds = int(seconds_float)
    millionths = int((seconds_float - seconds) * 1000000)
    
    return (degrees, minutes, seconds, millionths)


def set_site_geo_coords(ifc_file, latitude: float, longitude: float, elevation: Optional[float] = None):
    """Set the site's geographic coordinates to new values."""
    sites = ifc_file.by_type("IfcSite")
    if not sites:
        print("No IfcSite found in the model")
        return False
    
    site = sites[0]
    
    # Convert decimal degrees to IFC format
    lat_dms = decimal_to_dms(latitude)
    long_dms = decimal_to_dms(longitude)
    
    # Set the new coordinates
    site.RefLatitude = lat_dms
    site.RefLongitude = long_dms
    
    if elevation is not None and hasattr(site, "RefElevation"):
        site.RefElevation = elevation
    
    return True


def update_map_conversion(ifc_file, easting: float, northing: float, elevation: Optional[float] = None,
                          target_epsg: Optional[int] = None, rotation_angle: Optional[float] = None):
    """
    Update the IfcMapConversion with new eastings, northings, elevation, and optional rotation.
    Optionally also update the target CRS information if EPSG code is provided.
    """
    if target_epsg and not PYPROJ_AVAILABLE:
        print("WARNING: PyProj not available. EPSG code will not be fully utilized.")
    
    contexts = ifc_file.by_type("IfcGeometricRepresentationContext")
    map_conversion = None
    target_crs = None
    
    # Find existing map conversion
    for context in contexts:
        if hasattr(context, "HasCoordinateOperation") and context.HasCoordinateOperation:
            for op in context.HasCoordinateOperation:
                if op.is_a("IfcMapConversion"):
                    map_conversion = op
                    if hasattr(op, "TargetCRS"):
                        target_crs = op.TargetCRS
                    break
    
    # Calculate x-axis direction components if rotation is specified
    x_axis_abscissa = 1.0
    x_axis_ordinate = 0.0
    
    if rotation_angle is not None:
        # Convert rotation angle to radians
        angle_rad = math.radians(rotation_angle)
        x_axis_abscissa = math.cos(angle_rad)
        x_axis_ordinate = math.sin(angle_rad)
    
    # Check if we need to create a new map conversion
    if map_conversion is None:
        # Check if we have a suitable context
        world_context = None
        for context in contexts:
            if context.ContextType == 'Model' and context.ContextIdentifier == 'Body':
                world_context = context
                break
        
        if world_context is None and contexts:
            world_context = contexts[0]
        
        if world_context:
            # Create a new target CRS if needed and if PyProj is available
            if target_epsg and PYPROJ_AVAILABLE:
                try:
                    crs_data = CRS.from_epsg(target_epsg)
                    
                    # Create the unit for the CRS
                    unit_name = "METRE"
                    dimensions = ifc_file.createIfcDimensionalExponents(1, 0, 0, 0, 0, 0, 0)
                    unit = ifc_file.createIfcSIUnit(None, "LENGTHUNIT", None, unit_name)
                    
                    # Create the target CRS
                    target_crs = ifc_file.createIfcProjectedCRS(
                        Name=f"EPSG:{target_epsg}",
                        Description=crs_data.name,
                        GeodeticDatum=crs_data.datum.name if hasattr(crs_data, 'datum') and crs_data.datum else "Unknown",
                        MapProjection=crs_data.coordinate_operation.name if hasattr(crs_data, 'coordinate_operation') and crs_data.coordinate_operation else "Unknown",
                        MapZone=str(crs_data.utm_zone) if hasattr(crs_data, 'utm_zone') and crs_data.utm_zone else None,
                        MapUnit=unit
                    )
                except Exception as e:
                    print(f"WARNING: Failed to create CRS from EPSG code {target_epsg}: {e}")
                    target_crs = None
            
            # Create the map conversion
            map_conversion = ifc_file.createIfcMapConversion(
                world_context,
                target_crs,
                easting,
                northing,
                elevation if elevation is not None else 0.0,
                x_axis_abscissa,  # X axis abscissa with rotation
                x_axis_ordinate,  # X axis ordinate with rotation
                1.0   # Scale
            )
            
            # Link the map conversion to the context
            if not world_context.HasCoordinateOperation:
                world_context.HasCoordinateOperation = [map_conversion]
            else:
                world_context.HasCoordinateOperation.append(map_conversion)
    else:
        # Update existing map conversion
        map_conversion.Eastings = easting
        map_conversion.Northings = northing
        
        if elevation is not None and hasattr(map_conversion, "OrthogonalHeight"):
            map_conversion.OrthogonalHeight = elevation
        
        # Update rotation if specified
        if rotation_angle is not None and hasattr(map_conversion, "XAxisAbscissa") and hasattr(map_conversion, "XAxisOrdinate"):
            map_conversion.XAxisAbscissa = x_axis_abscissa
            map_conversion.XAxisOrdinate = x_axis_ordinate
        
        # Update target CRS if EPSG code is provided and PyProj is available
        if target_epsg and PYPROJ_AVAILABLE and target_crs:
            try:
                crs_data = CRS.from_epsg(target_epsg)
                target_crs.Name = f"EPSG:{target_epsg}"
                target_crs.Description = crs_data.name
                target_crs.GeodeticDatum = crs_data.datum.name if hasattr(crs_data, 'datum') and crs_data.datum else "Unknown"
                target_crs.MapProjection = crs_data.coordinate_operation.name if hasattr(crs_data, 'coordinate_operation') and crs_data.coordinate_operation else "Unknown"
                if hasattr(crs_data, 'utm_zone') and crs_data.utm_zone:
                    target_crs.MapZone = str(crs_data.utm_zone)
            except Exception as e:
                print(f"WARNING: Failed to update CRS from EPSG code {target_epsg}: {e}")
    
    return True


def get_transformation_from_wgs84_to_projected(source_lat: float, source_lon: float, source_elev: float,
                                              target_easting: float, target_northing: float, target_elev: float,
                                              target_epsg: int) -> Tuple[float, float, float]:
    """
    Calculate transformation from WGS84 coordinates to a specific projected coordinate system.
    Returns the translation offsets in X, Y, Z to be applied.
    """
    if not PYPROJ_AVAILABLE:
        print("WARNING: PyProj not available. Using simplified transformation.")
        # Simple approximation (not accurate)
        earth_radius = 6371000  # meters
        
        # Convert to radians
        source_lat_rad = math.radians(source_lat)
        source_lon_rad = math.radians(source_lon)
        
        # Calculate approximate X, Y in meters from origin (very rough approximation)
        source_x = earth_radius * math.cos(source_lat_rad) * math.cos(source_lon_rad)
        source_y = earth_radius * math.cos(source_lat_rad) * math.sin(source_lon_rad)
        
        # Define arbitrary target coordinates (this is very simplified)
        target_x = target_easting
        target_y = target_northing
        
        # Calculate the differences
        dx = target_x - source_x
        dy = target_y - source_y
        dz = target_elev - source_elev
        
        return dx, dy, dz
    else:
        # Use PyProj for accurate transformation
        wgs84 = CRS.from_epsg(4326)  # WGS84
        target_crs = CRS.from_epsg(target_epsg)
        
        # Create transformer from WGS84 to target CRS
        transformer = Transformer.from_crs(wgs84, target_crs, always_xy=True)
        
        # Transform source coordinates from WGS84 to target CRS
        source_easting, source_northing = transformer.transform(source_lon, source_lat)
        
        # Calculate the differences
        dx = target_easting - source_easting
        dy = target_northing - source_northing
        dz = target_elev - source_elev if target_elev is not None and source_elev is not None else 0
        
        return dx, dy, dz


def transform_between_projected_crs(source_easting: float, source_northing: float, source_elev: float,
                                  source_epsg: int, target_easting: float, target_northing: float, 
                                  target_elev: float, target_epsg: int) -> Tuple[float, float, float]:
    """
    Calculate transformation between two projected coordinate systems.
    Returns the translation offsets in X, Y, Z to be applied.
    """
    if not PYPROJ_AVAILABLE:
        print("WARNING: PyProj not available. Using direct offsets between coordinates.")
        # Simple coordinate differences
        dx = target_easting - source_easting
        dy = target_northing - source_northing
        dz = target_elev - source_elev if target_elev is not None and source_elev is not None else 0
        
        return dx, dy, dz
    else:
        # Use PyProj for accurate transformation
        source_crs = CRS.from_epsg(source_epsg)
        target_crs = CRS.from_epsg(target_epsg)
        
        # Create transformer from source CRS to target CRS
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        
        # Transform source coordinates from source CRS to target CRS
        transformed_easting, transformed_northing = transformer.transform(source_easting, source_northing)
        
        # Calculate the differences between transformed coordinates and target coordinates
        dx = target_easting - transformed_easting
        dy = target_northing - transformed_northing
        dz = target_elev - source_elev if target_elev is not None and source_elev is not None else 0
        
        return dx, dy, dz


def move_model_elements(ifc_file, dx: float, dy: float, dz: float, rotation_angle: Optional[float] = None):
    """
    Move and rotate all elements in the model by applying a transformation to the site placement.
    
    Args:
        ifc_file: The IFC file
        dx, dy, dz: Translation vector components
        rotation_angle: Optional rotation angle around the Z axis in degrees (clockwise positive)
    """
    sites = ifc_file.by_type("IfcSite")
    if not sites:
        return False
    
    site = sites[0]
    placement = site.ObjectPlacement
    
    if placement.is_a("IfcLocalPlacement"):
        # Create translation point
        translation = ifc_file.createIfcCartesianPoint([dx, dy, dz])
        
        # Define axis directions
        if rotation_angle is not None:
            # Convert rotation angle to radians
            angle_rad = math.radians(rotation_angle)
            
            # Create rotation matrix around Z axis
            # X direction vector (rotated)
            x_direction = ifc_file.createIfcDirection([math.cos(angle_rad), math.sin(angle_rad), 0.0])
            
            # Y direction vector (rotated)
            y_direction = ifc_file.createIfcDirection([-math.sin(angle_rad), math.cos(angle_rad), 0.0])
            
            # Z direction remains [0,0,1]
            z_direction = ifc_file.createIfcDirection([0.0, 0.0, 1.0])
            
            # Create 3D axis with rotation
            axis = ifc_file.createIfcAxis2Placement3D(translation, z_direction, x_direction)
        else:
            # No rotation, use default axis directions
            direction = ifc_file.createIfcDirection([1.0, 0.0, 0.0])
            axis = ifc_file.createIfcAxis2Placement3D(translation, None, direction)
        
        # Apply the transformation to the site placement
        placement.RelativePlacement = axis
        
        return True
    else:
        print("Site placement is not an IfcLocalPlacement. More complex transformations required.")
        return False


def relocate_ifc_model(input_file: str, output_file: str, 
                     target_coords: Dict[str, Any],
                     source_epsg: Optional[int] = None,
                     target_epsg: Optional[int] = None,
                     rotation_angle: Optional[float] = None):
    """
    Main function to relocate and rotate an IFC model using various coordinate systems.
    
    Args:
        input_file: Path to the input IFC file
        output_file: Path to save the relocated IFC file
        target_coords: Dictionary containing target coordinates:
            - If using lat/lon: 'latitude', 'longitude', 'elevation' (optional)
            - If using projected: 'easting', 'northing', 'elevation' (optional)
        source_epsg: EPSG code for the source coordinate system (optional)
        target_epsg: EPSG code for the target coordinate system (optional)
        rotation_angle: Rotation angle around Z axis in degrees (optional)
    """
    # Check if PyProj is available when EPSG codes are provided
    if (source_epsg or target_epsg) and not PYPROJ_AVAILABLE:
        print("WARNING: PyProj not installed. EPSG transformations will be approximate.")
    
    # Load the IFC file
    print(f"Loading IFC file: {input_file}")
    ifc_file = ifcopenshell.open(input_file)
    
    # Get current geographic location
    geo_data = get_site_geo_data(ifc_file)
    if not geo_data:
        print("No geographic data found in the IFC file")
        return False
    
    print("\nCurrent geographic location:")
    print(f"  Latitude: {geo_data['latitude']}")
    print(f"  Longitude: {geo_data['longitude']}")
    print(f"  Elevation: {geo_data['elevation']}")
    
    if geo_data['map_conversion']:
        print("\nCurrent map conversion:")
        print(f"  Eastings: {geo_data['map_conversion']['eastings']}")
        print(f"  Northings: {geo_data['map_conversion']['northings']}")
        print(f"  Height: {geo_data['map_conversion']['orthogonal_height']}")
        print(f"  X-Axis Abscissa: {geo_data['map_conversion']['x_axis_abscissa']}")
        print(f"  X-Axis Ordinate: {geo_data['map_conversion']['x_axis_ordinate']}")
        
        # Calculate current rotation if available
        if (geo_data['map_conversion']['x_axis_abscissa'] is not None and 
            geo_data['map_conversion']['x_axis_ordinate'] is not None):
            current_rotation = math.degrees(math.atan2(
                geo_data['map_conversion']['x_axis_ordinate'],
                geo_data['map_conversion']['x_axis_abscissa']
            ))
            print(f"  Current rotation: {current_rotation:.2f} degrees")
        
        if geo_data['projected_crs']:
            print("\nCurrent projected CRS:")
            print(f"  Name: {geo_data['projected_crs']['name']}")
            print(f"  EPSG code: {geo_data['epsg_code']}")
    
    # Determine source EPSG code
    actual_source_epsg = geo_data['epsg_code'] if geo_data['epsg_code'] else source_epsg
    if not actual_source_epsg:
        actual_source_epsg = 4326  # Default to WGS84
        print(f"\nNo source EPSG code found in file or provided. Using default: {actual_source_epsg} (WGS84)")
    else:
        print(f"\nUsing source EPSG code: {actual_source_epsg}")
    
    # Determine if we're working with geographic or projected coordinates
    using_projected_coords = 'easting' in target_coords and 'northing' in target_coords
    
    # Print rotation information if provided
    if rotation_angle is not None:
        print(f"\nRotation angle: {rotation_angle} degrees")
    
    # Calculate transformation
    print("\nCalculating coordinate transformation...")
    dx, dy, dz = 0, 0, 0
    
    if using_projected_coords:
        # Using projected coordinates (eastings, northings)
        target_easting = target_coords['easting']
        target_northing = target_coords['northing']
        target_elev = target_coords.get('elevation', geo_data['elevation'])
        actual_target_epsg = target_epsg
        
        if not actual_target_epsg:
            print("ERROR: When using projected coordinates, target_epsg must be provided.")
            return False
        
        print(f"Target coordinates (EPSG:{actual_target_epsg}):")
        print(f"  Easting: {target_easting}")
        print(f"  Northing: {target_northing}")
        print(f"  Elevation: {target_elev}")
        
        if geo_data['map_conversion']:
            # We have existing map conversion data
            source_easting = geo_data['map_conversion']['eastings']
            source_northing = geo_data['map_conversion']['northings']
            source_elev = geo_data['map_conversion']['orthogonal_height']
            
            # Transform between projected CRS
            dx, dy, dz = transform_between_projected_crs(
                source_easting, source_northing, source_elev,
                actual_source_epsg, target_easting, target_northing, 
                target_elev, actual_target_epsg
            )
        else:
            # We only have geographic coordinates, need to transform from WGS84
            dx, dy, dz = get_transformation_from_wgs84_to_projected(
                geo_data['latitude'], geo_data['longitude'], geo_data['elevation'],
                target_easting, target_northing, target_elev,
                actual_target_epsg
            )
        
        # Update map conversion data
        update_map_conversion(ifc_file, target_easting, target_northing, target_elev, actual_target_epsg, rotation_angle)
        
        # If we have PyProj, also update the WGS84 coordinates
        if PYPROJ_AVAILABLE:
            try:
                target_crs = CRS.from_epsg(actual_target_epsg)
                wgs84 = CRS.from_epsg(4326)
                transformer = Transformer.from_crs(target_crs, wgs84, always_xy=True)
                lon, lat = transformer.transform(target_easting, target_northing)
                
                # Update the geographic coordinates
                set_site_geo_coords(ifc_file, lat, lon, target_elev)
                print(f"\nUpdated geographic coordinates:")
                print(f"  Latitude: {lat}")
                print(f"  Longitude: {lon}")
            except Exception as e:
                print(f"WARNING: Could not update geographic coordinates: {e}")
    else:
        # Using geographic coordinates (latitude, longitude)
        target_lat = target_coords['latitude']
        target_lon = target_coords['longitude']
        target_elev = target_coords.get('elevation', geo_data['elevation'])
        
        print(f"Target geographic coordinates (WGS84):")
        print(f"  Latitude: {target_lat}")
        print(f"  Longitude: {target_lon}")
        print(f"  Elevation: {target_elev}")
        
        # Calculate approximate delta using simple method
        earth_radius = 6371000  # meters
        
        # Convert to radians
        old_lat_rad = math.radians(geo_data['latitude']) if geo_data['latitude'] else 0
        old_lon_rad = math.radians(geo_data['longitude']) if geo_data['longitude'] else 0
        new_lat_rad = math.radians(target_lat)
        new_lon_rad = math.radians(target_lon)
        
        # Calculate displacement (approximate)
        dx = earth_radius * math.cos(new_lat_rad) * (new_lon_rad - old_lon_rad)
        dy = earth_radius * (new_lat_rad - old_lat_rad)
        dz = target_elev - geo_data['elevation'] if geo_data['elevation'] is not None and target_elev is not None else 0
        
        # Update the geographic coordinates
        set_site_geo_coords(ifc_file, target_lat, target_lon, target_elev)
        
        # If we have map conversion and target EPSG, update the projected coordinates too
        if geo_data['map_conversion'] and target_epsg and PYPROJ_AVAILABLE:
            try:
                wgs84 = CRS.from_epsg(4326)
                target_crs = CRS.from_epsg(target_epsg)
                transformer = Transformer.from_crs(wgs84, target_crs, always_xy=True)
                easting, northing = transformer.transform(target_lon, target_lat)
                
                # Update map conversion
                update_map_conversion(ifc_file, easting, northing, target_elev, target_epsg, rotation_angle)
                print(f"\nUpdated projected coordinates (EPSG:{target_epsg}):")
                print(f"  Easting: {easting}")
                print(f"  Northing: {northing}")
            except Exception as e:
                print(f"WARNING: Could not update projected coordinates: {e}")
    
    print(f"\nCalculated transformation: dx={dx:.2f}m, dy={dy:.2f}m, dz={dz:.2f}m")
    if rotation_angle is not None:
        print(f"Rotation: {rotation_angle} degrees around Z axis")
    
    # Move and rotate the model elements
    if move_model_elements(ifc_file, dx, dy, dz, rotation_angle):
        print("Successfully moved and rotated model elements")
    else:
        print("Failed to move model elements")
        return False
    
    # Save the updated IFC file
    ifc_file.write(output_file)
    print(f"\nSuccessfully saved relocated IFC model to: {output_file}")
    
    return True


if __name__ == "__main__":
    # Usage: python IFCgeoreferencer.py TestVilla_helsinki.ifc TestVilla_helsinki_moved_rotated.ifc --projected --easting -920163 --northing 6956137 --target-epsg 3857 --rotation 45 --elevation 3
	parser = argparse.ArgumentParser(description="Relocate and rotate an IFC model to new geographic coordinates or projected coordinates")
    parser.add_argument("input_file", help="Path to the input IFC file")
    parser.add_argument("output_file", help="Path to save the relocated IFC file")
    
    # Create mutually exclusive group for coordinate type
    coord_group = parser.add_mutually_exclusive_group(required=True)
    coord_group.add_argument("--geographic", action="store_true", help="Use geographic coordinates (latitude/longitude)")
    coord_group.add_argument("--projected", action="store_true", help="Use projected coordinates (easting/northing)")
    
    # Geographic coordinates
    geo_group = parser.add_argument_group("Geographic coordinates")
    geo_group.add_argument("--latitude", type=float, help="Target latitude in decimal degrees")
    geo_group.add_argument("--longitude", type=float, help="Target longitude in decimal degrees")
    
    # Projected coordinates
    proj_group = parser.add_argument_group("Projected coordinates")
    proj_group.add_argument("--easting", type=float, help="Target easting in meters")
    proj_group.add_argument("--northing", type=float, help="Target northing in meters")
    
    # Common parameters
    parser.add_argument("--elevation", type=float, help="Target elevation in meters (optional)")
    parser.add_argument("--source-epsg", type=int, help="Source EPSG code (optional, will try to extract from file)")
    parser.add_argument("--target-epsg", type=int, help="Target EPSG code (required for projected coordinates)")
    
    # Rotation parameter
    # Rotation parameter
    parser.add_argument("--rotation", type=float, help="Rotation angle in degrees clockwise around Z axis (optional)")
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.geographic and (args.latitude is None or args.longitude is None):
        parser.error("When using --geographic, both --latitude and --longitude must be provided")
    
    if args.projected and (args.easting is None or args.northing is None or args.target_epsg is None):
        parser.error("When using --projected, --easting, --northing, and --target-epsg must be provided")
    
    # Prepare coordinates dictionary based on coordinate type
    target_coords = {}
    if args.geographic:
        target_coords = {
            'latitude': args.latitude,
            'longitude': args.longitude
        }
    else:  # args.projected
        target_coords = {
            'easting': args.easting,
            'northing': args.northing
        }
    
    # Add elevation if provided
    if args.elevation is not None:
        target_coords['elevation'] = args.elevation
    
    # Call the relocation function
    success = relocate_ifc_model(
        args.input_file,
        args.output_file,
        target_coords,
        source_epsg=args.source_epsg,
        target_epsg=args.target_epsg,
        rotation_angle=args.rotation
    )
    
    # Exit with appropriate status code
    sys.exit(0 if success else 1)