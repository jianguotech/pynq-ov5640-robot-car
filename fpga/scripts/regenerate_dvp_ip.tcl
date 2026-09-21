# Regenerate OV5640 DVP IP with Vivado-standard AXI4-Lite slave
# This creates a clean IP with Vivado-generated S00_AXI (no hand modifications)
# Then replaces the user logic with our clean capture + core

set IP_DIR "D:/Vitis/ip_repo/myOV5640_DVP_clean"
set SRC_DIR "D:/Vitis/ip_repo/myOV5640_DVP_1_0/src"

puts "=== Creating clean DVP IP ==="
file delete -force $IP_DIR
file mkdir $IP_DIR
file mkdir [file join $IP_DIR src]

# Use Vivado's create_peripheral to generate AXI4-Lite template
create_project dvp_temp D:/Vitis/dvp_temp -part xc7z020clg400-1 -force

# Create AXI4 Peripheral
# This generates the standard Vivado S00_AXI boilerplate
ipx::create_peripheral -name myOV5640_DVP -vendor xilinx.com -library user -version 1.0 \
    -dir $IP_DIR \
    -bus_interfaces s00_axi:aximm:S_AXI:SLAVE

# At this point, Vivado has generated:
#   src/myOV5640_DVP_v1_0_S00_AXI.v  (standard AXI4-Lite template)
#   src/myOV5640_DVP_v1_0.v          (top-level wrapper)
#   component.xml                     (IP definition)

# Now add our custom user logic files
file copy [file join $SRC_DIR ov5640_dvp_capture.v] [file join $IP_DIR src ov5640_dvp_capture.v]
file copy [file join $SRC_DIR ov5640_dvp_core.v]     [file join $IP_DIR src ov5640_dvp_core.v]

puts "=== DONE - IP regenerated at $IP_DIR ==="
close_project
