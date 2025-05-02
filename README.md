# IFCgeoreferencer
IFCgeoreferencer is a python script that can be used to move an IFC file to a coordinate, reproject the EPSG used for georeferencing and also rotate the IFC file around the Z-axis.

# Pre-requirements
The script is tested with Python 3.10, relies on pyproj, ifcopenshell and numpy for variuos operations.

# Usage
Example 1: Move to new geographic coordinates (latitude/longitude)

python IFCgeoreferencer.py input.ifc output.ifc --geographic --latitude 43.65107 --longitude -79.347015 --elevation 100

This command will:
- Move the IFC model from its current location to the specified latitude/longitude
- Set the elevation to 100 meters
- Update both the geographic coordinates and any existing projected coordinates if possible

Example 2: Move to new projected coordinates with EPSG code

python IFCgeoreferencer.py input.ifc output.ifc --projected --easting 630084.06 --northing 4834438.59 --target-epsg 32617 --elevation 100

This command will:
- Move the IFC model to the new easting/northing coordinates in the UTM Zone 17N coordinate system (EPSG:32617)
- Set the elevation to 100 meters
- Also update the geographic coordinates (latitude/longitude) based on the new projected location

Example 3: Move and rotate the model

python IFCgeoreferencer.py input.ifc output.ifc --geographic --latitude 40.7128 --longitude -74.006 --rotation 45

This command will:
- Move the IFC model to the new geographic location (New York City)
- Rotate the model 45 degrees clockwise around the Z axis

Example 4: Move between different coordinate systems with explicit source EPSG

python IFCgeoreferencer.py input.ifc output.ifc --projected --easting 500000 --northing 4000000 --source-epsg 25832 --target-epsg 32633

This command will:
- Explicitly specify that the source coordinates are in EPSG:25832 (ETRS89 / UTM zone 32N)
- Move to the new coordinates in EPSG:32633 (WGS 84 / UTM zone 33N)
- Perform the necessary coordinate transformation between the coordinate systems

Example 5: Simple rotation without changing location

python IFCgeoreferencer.py input.ifc output.ifc --geographic --latitude $(existing_lat) --longitude $(existing_lon) --rotation 90

This command would rotate the model 90 degrees while keeping it at the same location. You would need to replace $(existing_lat) and $(existing_lon) with the actual existing coordinates of the model.
