# Reference architecture: v_vid_in_axi4s + VDMA + SmartConnect + our pins
# Adapted from xupsh/Pynq-CV-OV5640 for PYNQ-Z1 + Vivado 2023.2
set ROOT [file normalize [file join [file dirname [info script]] ".."]]
set PROJECT_DIR "D:/Vitis/OV5640_REF"
set IP_REPO "D:/Vitis/ip_repo"
set BOARD_DIR [file join $ROOT board_files]
set PRESET_TCL [file join $BOARD_DIR pynq_revC.tcl]

puts "=== BUILD REF ARCH ==="
file mkdir $PROJECT_DIR
set_param board.repoPaths $BOARD_DIR
create_project OV5640_REF $PROJECT_DIR -part xc7z020clg400-1 -force
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list $IP_REPO] [current_project]
update_ip_catalog

add_files -norecurse "C:/Users/czy/fpga-workspace/src/sda_iobuf.v"
add_files -norecurse "C:/Users/czy/fpga-workspace/src/scl_obuft.v"
update_compile_order -fileset sources_1

puts "=== BUILD BD ==="
create_bd_design "design_1"

# PS
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0
source $PRESET_TCL
set_property -dict [apply_preset processing_system7_0] [get_bd_cells processing_system7_0]
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {32}] [get_bd_cells processing_system7_0]

# GP0 interconnect
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph
set_property CONFIG.NUM_MI {1} [get_bd_cells ps7_0_axi_periph]

# SmartConnect for VDMA→HP0
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_mem_intercon
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells axi_mem_intercon]

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M

# === KEY: v_vid_in_axi4s - Xilinx standard DVP→AXI-Stream (like reference) ===
create_bd_cell -type ip -vlnv xilinx.com:ip:v_vid_in_axi4s:5.0 vid_in_0
set_property -dict [list \
    CONFIG.C_HAS_ASYNC_CLK {1} \
    CONFIG.C_M_AXIS_VIDEO_FORMAT {2} \
] [get_bd_cells vid_in_0]

# vid_in uses default settings - no AXI-Lite needed

# === KEY: VDMA for frame capture (like reference) ===
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 axi_vdma_0
set_property -dict [list \
    CONFIG.c_include_mm2s {0} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_num_fstores {3} \
    CONFIG.c_m_axi_s2mm_data_width {32} \
    CONFIG.c_s2mm_linebuffer_depth {4096} \
] [get_bd_cells axi_vdma_0]

# SCCB (classmate's IP)
create_bd_cell -type ip -vlnv bjut_group3_wyf:user:sccb_master:1.0 sccb_master_0
create_bd_cell -type ip -vlnv bjut_group3_wyf:user:ov5640_cfg_init_ctrl:1.0 cfg_init_ctrl_0
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 cfg_start_one
set_property -dict [list CONFIG.CONST_VAL {1} CONFIG.CONST_WIDTH {1}] [get_bd_cells cfg_start_one]
create_bd_cell -type module -reference sda_iobuf sda_iobuf_0
create_bd_cell -type module -reference scl_obuft scl_obuft_0

# Clock wizard for camera XCLK
create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0
set_property -dict [list CONFIG.PRIM_SOURCE {No_buffer} CONFIG.PRIM_IN_FREQ {100.000} CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {24.000} CONFIG.USE_LOCKED {true} CONFIG.USE_RESET {false}] [get_bd_cells clk_wiz_0]

# Ports
create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR
create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO
create_bd_port -dir I -from 7 -to 0 cam_data_i
create_bd_port -dir I cam_pclk_i; create_bd_port -dir I cam_href_i; create_bd_port -dir I cam_vsync_i
create_bd_port -dir O cam_xclk_o; create_bd_port -dir O cam_resetb_o; create_bd_port -dir O cam_pwdn_o
create_bd_port -dir O -from 3 -to 0 dbg_led_o

# ==================== CONNECTIONS ====================
connect_bd_intf_net [get_bd_intf_ports DDR] [get_bd_intf_pins processing_system7_0/DDR]
connect_bd_intf_net [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins processing_system7_0/FIXED_IO]

# GP0: M00→VDMA (vid_in needs no AXI-Lite - uses defaults)
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ps7_0_axi_periph/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M00_AXI] [get_bd_intf_pins axi_vdma_0/S_AXI_LITE]

# Clock
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
    [get_bd_pins ps7_0_axi_periph/ACLK] [get_bd_pins ps7_0_axi_periph/S00_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M00_ACLK] \
    [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
    [get_bd_pins clk_wiz_0/clk_in1] [get_bd_pins sccb_master_0/clk] [get_bd_pins cfg_init_ctrl_0/clk] \
    [get_bd_pins vid_in_0/aclk] \
    [get_bd_pins axi_vdma_0/m_axi_s2mm_aclk] [get_bd_pins axi_vdma_0/s_axi_lite_aclk] [get_bd_pins axi_vdma_0/s_axis_s2mm_aclk] \
    [get_bd_pins axi_mem_intercon/aclk] \
    [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]

# Reset
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins ps7_0_axi_periph/ARESETN] [get_bd_pins ps7_0_axi_periph/S00_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M00_ARESETN] \
    [get_bd_pins sccb_master_0/rst_n] [get_bd_pins cfg_init_ctrl_0/rst_n] \
    [get_bd_pins vid_in_0/aresetn] \
    [get_bd_pins axi_vdma_0/axi_resetn] [get_bd_pins axi_mem_intercon/aresetn]

# Camera signals → vid_in
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_ports cam_xclk_o]
connect_bd_net [get_bd_ports cam_data_i]  [get_bd_pins vid_in_0/vid_data]
connect_bd_net [get_bd_ports cam_pclk_i]  [get_bd_pins vid_in_0/vid_io_in_clk]
connect_bd_net [get_bd_ports cam_href_i]  [get_bd_pins vid_in_0/vid_active_video]
connect_bd_net [get_bd_ports cam_vsync_i] [get_bd_pins vid_in_0/vid_vsync]

# vid_in hsync tie-off (not used, tie to 0)
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 hsync_gnd
set_property -dict [list CONFIG.CONST_VAL {0} CONFIG.CONST_WIDTH {1}] [get_bd_cells hsync_gnd]
connect_bd_net [get_bd_pins hsync_gnd/dout] [get_bd_pins vid_in_0/vid_hsync]

# AXI-Stream: vid_in → VDMA
connect_bd_intf_net [get_bd_intf_pins vid_in_0/video_out] [get_bd_intf_pins axi_vdma_0/S_AXIS_S2MM]

# VDMA → HP0
connect_bd_intf_net [get_bd_intf_pins axi_vdma_0/M_AXI_S2MM] [get_bd_intf_pins axi_mem_intercon/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_mem_intercon/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]

# Camera controls
connect_bd_net [get_bd_pins sccb_master_0/ov_reset_n] [get_bd_ports cam_resetb_o]
connect_bd_net [get_bd_pins sccb_master_0/ov_pwdn] [get_bd_ports cam_pwdn_o]

# LED
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_ports dbg_led_o]

# SCCB connections
connect_bd_net [get_bd_pins cfg_init_ctrl_0/sccb_cmd_valid] [get_bd_pins sccb_master_0/cmd_valid]
connect_bd_net [get_bd_pins sccb_master_0/cmd_ready] [get_bd_pins cfg_init_ctrl_0/sccb_cmd_ready]
connect_bd_net [get_bd_pins cfg_init_ctrl_0/sccb_cmd_rw] [get_bd_pins sccb_master_0/cmd_rw]
connect_bd_net [get_bd_pins cfg_init_ctrl_0/sccb_dev_addr] [get_bd_pins sccb_master_0/dev_addr]
connect_bd_net [get_bd_pins cfg_init_ctrl_0/sccb_reg_addr] [get_bd_pins sccb_master_0/reg_addr]
connect_bd_net [get_bd_pins cfg_init_ctrl_0/sccb_wr_data] [get_bd_pins sccb_master_0/wr_data]
connect_bd_net [get_bd_pins sccb_master_0/rd_data] [get_bd_pins cfg_init_ctrl_0/sccb_rd_data]
connect_bd_net [get_bd_pins sccb_master_0/done] [get_bd_pins cfg_init_ctrl_0/sccb_done]
connect_bd_net [get_bd_pins sccb_master_0/error] [get_bd_pins cfg_init_ctrl_0/sccb_error]
connect_bd_net [get_bd_pins sccb_master_0/camera_ready] [get_bd_pins cfg_init_ctrl_0/camera_ready]
connect_bd_net [get_bd_pins cfg_start_one/dout] [get_bd_pins cfg_init_ctrl_0/start]
connect_bd_net [get_bd_pins sccb_master_0/sioc_o] [get_bd_pins scl_obuft_0/scl_i]
connect_bd_net [get_bd_pins sccb_master_0/sioc_t] [get_bd_pins scl_obuft_0/scl_t]
make_bd_pins_external [get_bd_pins scl_obuft_0/scl_pin]
connect_bd_net [get_bd_pins sccb_master_0/siod_o] [get_bd_pins sda_iobuf_0/sda_i_fpga]
connect_bd_net [get_bd_pins sccb_master_0/siod_t] [get_bd_pins sda_iobuf_0/sda_t_fpga]
connect_bd_net [get_bd_pins sda_iobuf_0/sda_o_fpga] [get_bd_pins sccb_master_0/siod_i]
make_bd_pins_external [get_bd_pins sda_iobuf_0/sda_pin]
foreach p [get_bd_ports] {
    set n [get_property NAME $p]
    if {[string match "*sda_pin*" $n]} { set_property name ov_sda $p }
    if {[string match "*scl_pin*" $n]} { set_property name ov_scl $p }
}

validate_bd_design
assign_bd_address
save_bd_design

set bd_file [get_files [file join $PROJECT_DIR OV5640_REF.srcs sources_1 bd design_1 design_1.bd]]
generate_target all $bd_file
make_wrapper -files $bd_file -top
add_files -norecurse [file join $PROJECT_DIR OV5640_REF.gen sources_1 bd design_1 hdl design_1_wrapper.v]
set_property top design_1_wrapper [current_fileset]
update_compile_order -fileset sources_1

# XDC with OUR pinout
set xdc_path "C:/Users/czy/fpga-workspace/constraints/pynq_ref.xdc"
set fd [open $xdc_path w]
puts $fd {## Ref arch - our pinout
set_property -dict { PACKAGE_PIN R14 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[0] }];
set_property -dict { PACKAGE_PIN P14 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[1] }];
set_property -dict { PACKAGE_PIN N16 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[2] }];
set_property -dict { PACKAGE_PIN M14 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[3] }];
set_property -dict { PACKAGE_PIN W18 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[0] }];
set_property -dict { PACKAGE_PIN Y16 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[1] }];
set_property -dict { PACKAGE_PIN W19 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[2] }];
set_property -dict { PACKAGE_PIN Y17 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[3] }];
set_property -dict { PACKAGE_PIN V16 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[4] }];
set_property -dict { PACKAGE_PIN W14 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[5] }];
set_property -dict { PACKAGE_PIN W16 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[6] }];
set_property -dict { PACKAGE_PIN Y14 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[7] }];
set_property -dict { PACKAGE_PIN V12 IOSTANDARD LVCMOS33 } [get_ports { cam_href_i   }];
set_property -dict { PACKAGE_PIN T11 IOSTANDARD LVCMOS33 } [get_ports { cam_vsync_i  }];
set_property -dict { PACKAGE_PIN U18 IOSTANDARD LVCMOS33 } [get_ports { cam_pclk_i   }];
set_property -dict { PACKAGE_PIN U19 IOSTANDARD LVCMOS33 } [get_ports { cam_xclk_o   }];
set_property -dict { PACKAGE_PIN Y19 IOSTANDARD LVCMOS33 } [get_ports { cam_resetb_o }];
set_property -dict { PACKAGE_PIN Y18 IOSTANDARD LVCMOS33 } [get_ports { cam_pwdn_o   }];
set_property -dict { PACKAGE_PIN W13 IOSTANDARD LVCMOS33 PULLUP true } [get_ports { ov_sda }];
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 PULLUP true } [get_ports { ov_scl }];
create_clock -name cam_pclk -period 17.857 [get_ports { cam_pclk_i }];
set_clock_groups -asynchronous -group [get_clocks cam_pclk] -group [get_clocks -include_generated_clocks clk_fpga_0];
set_input_delay -clock [get_clocks cam_pclk] -min 0.000 [get_ports { cam_data_i[*] cam_href_i cam_vsync_i }];
set_input_delay -clock [get_clocks cam_pclk] -max 8.000 [get_ports { cam_data_i[*] cam_href_i cam_vsync_i }];
set_false_path -to [get_ports { dbg_led_o[*] }];}
close $fd
add_files -fileset constrs_1 $xdc_path
set_property SEVERITY {Warning} [get_drc_checks NSTD-1]
set_property SEVERITY {Warning} [get_drc_checks UCIO-1]

puts "=== BUILD (~6 min) ==="
reset_run synth_1; launch_runs synth_1 -jobs 4; wait_on_run synth_1
reset_run impl_1; launch_runs impl_1 -to_step write_bitstream -jobs 4; wait_on_run impl_1
puts "=== DONE ==="
close_project
