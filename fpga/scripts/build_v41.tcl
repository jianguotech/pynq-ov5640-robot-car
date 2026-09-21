# v37: VDMA + SmartConnect + DVP + SCCB + tuser/tkeep connected

set ROOT [file normalize [file join [file dirname [info script]] ".."]]
set IP_REPO "D:/Vitis/ip_repo"
set BOARD_DIR [file join $ROOT board_files]
set PRESET_TCL [file join $BOARD_DIR pynq_revC.tcl]

puts "=== BUILD v37 VDMA ==="
# Keep old builds - use timestamped project name
set TIMESTAMP [clock format [clock seconds] -format "%Y%m%d_%H%M%S"]
set PROJECT_DIR "D:/Vitis/OV5640_V41_POR"
file mkdir $PROJECT_DIR
set_param board.repoPaths $BOARD_DIR
create_project OV5640_DVP_HW $PROJECT_DIR -part xc7z020clg400-1 -force
set_property board_part "www.digilentinc.com:pynq-z1:part0:1.0" [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list $IP_REPO] [current_project]
update_ip_catalog

add_files -norecurse "C:/Users/czy/fpga-workspace/src/sda_iobuf.v"
add_files -norecurse "C:/Users/czy/fpga-workspace/src/scl_obuft.v"
update_compile_order -fileset sources_1

puts "=== BUILD BD ==="
create_bd_design "design_1"

create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0
source $PRESET_TCL
set_property -dict [apply_preset processing_system7_0] [get_bd_cells processing_system7_0]
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {32}] [get_bd_cells processing_system7_0]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph
set_property CONFIG.NUM_MI {2} [get_bd_cells ps7_0_axi_periph]

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M

create_bd_cell -type ip -vlnv xilinx.com:user:myOV5640_DVP:1.1 myOV5640_DVP_0

create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0
set_property -dict [list CONFIG.PRIM_SOURCE {No_buffer} CONFIG.PRIM_IN_FREQ {100.000} CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {24.000} CONFIG.USE_LOCKED {true} CONFIG.USE_RESET {false}] [get_bd_cells clk_wiz_0]

create_bd_cell -type ip -vlnv bjut_group3_wyf:user:sccb_master:1.0 sccb_master_0
create_bd_cell -type ip -vlnv bjut_group3_wyf:user:ov5640_cfg_init_ctrl:1.0 cfg_init_ctrl_0
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 cfg_start_one
set_property -dict [list CONFIG.CONST_VAL {1} CONFIG.CONST_WIDTH {1}] [get_bd_cells cfg_start_one]

create_bd_cell -type module -reference sda_iobuf sda_iobuf_0
create_bd_cell -type module -reference scl_obuft scl_obuft_0

# SmartConnect for DMA→HP0 memory path (better AXI4→AXI3 handling)
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_mem_intercon
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells axi_mem_intercon]

# VDMA for video frame capture
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_vdma_0
set_property -dict [list \
    CONFIG.c_include_mm2s {0} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_include_sg {0} \
    CONFIG.c_s_axis_s2mm_tdata_width {16} \
    CONFIG.c_m_axi_s2mm_data_width {32} \
    CONFIG.c_s2mm_burst_size {16} \
    CONFIG.c_sg_length_width {23} \
] [get_bd_cells axi_vdma_0]

# tkeep constant: 2-bit 0b11 for 16-bit stream (both bytes valid)
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tkeep_ones
set_property -dict [list CONFIG.CONST_VAL {3} CONFIG.CONST_WIDTH {2}] [get_bd_cells tkeep_ones]

create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR
create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO
create_bd_port -dir I -from 7 -to 0 cam_data_i
create_bd_port -dir I cam_pclk_i; create_bd_port -dir I cam_href_i; create_bd_port -dir I cam_vsync_i
create_bd_port -dir O cam_xclk_o; create_bd_port -dir O cam_resetb_o; create_bd_port -dir O cam_pwdn_o
create_bd_port -dir O -from 3 -to 0 dbg_led_o

connect_bd_intf_net [get_bd_intf_ports DDR] [get_bd_intf_pins processing_system7_0/DDR]
connect_bd_intf_net [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins processing_system7_0/FIXED_IO]

connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ps7_0_axi_periph/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M00_AXI] [get_bd_intf_pins myOV5640_DVP_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M01_AXI] [get_bd_intf_pins axi_vdma_0/S_AXI_LITE]

connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
    [get_bd_pins ps7_0_axi_periph/ACLK] [get_bd_pins ps7_0_axi_periph/S00_ACLK] [get_bd_pins ps7_0_axi_periph/M00_ACLK] [get_bd_pins ps7_0_axi_periph/M01_ACLK] \
    [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] [get_bd_pins myOV5640_DVP_0/s00_axi_aclk] \
    [get_bd_pins clk_wiz_0/clk_in1] [get_bd_pins sccb_master_0/clk] [get_bd_pins cfg_init_ctrl_0/clk] \
    [get_bd_pins axi_vdma_0/m_axi_s2mm_aclk] [get_bd_pins axi_vdma_0/s_axi_lite_aclk] \
    [get_bd_pins axi_mem_intercon/aclk] \
    [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]

connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins ps7_0_axi_periph/ARESETN] [get_bd_pins ps7_0_axi_periph/S00_ARESETN] [get_bd_pins ps7_0_axi_periph/M00_ARESETN] [get_bd_pins ps7_0_axi_periph/M01_ARESETN] \
    [get_bd_pins myOV5640_DVP_0/s00_axi_aresetn] [get_bd_pins sccb_master_0/rst_n] [get_bd_pins cfg_init_ctrl_0/rst_n] \
    [get_bd_pins axi_vdma_0/axi_resetn] \
    [get_bd_pins axi_mem_intercon/aresetn]

connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins myOV5640_DVP_0/xclk_locked_i]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_ports cam_xclk_o]

connect_bd_net [get_bd_ports cam_data_i] [get_bd_pins myOV5640_DVP_0/cam_data_i]
connect_bd_net [get_bd_ports cam_pclk_i] [get_bd_pins myOV5640_DVP_0/cam_pclk_i]
connect_bd_net [get_bd_ports cam_href_i] [get_bd_pins myOV5640_DVP_0/cam_href_i]
connect_bd_net [get_bd_ports cam_vsync_i] [get_bd_pins myOV5640_DVP_0/cam_vsync_i]
connect_bd_net [get_bd_ports dbg_led_o] [get_bd_pins myOV5640_DVP_0/dbg_led_o]

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

connect_bd_net [get_bd_pins cfg_init_ctrl_0/init_done] [get_bd_pins myOV5640_DVP_0/sccb_init_done_i]
connect_bd_net [get_bd_pins cfg_init_ctrl_0/cfg_error] [get_bd_pins myOV5640_DVP_0/sccb_cfg_error_i]

connect_bd_net [get_bd_pins sccb_master_0/ov_reset_n] [get_bd_ports cam_resetb_o]
connect_bd_net [get_bd_pins sccb_master_0/ov_pwdn] [get_bd_ports cam_pwdn_o]

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

# AXI-Stream: DVP → DMA (direct, individual signals)
connect_bd_net [get_bd_pins myOV5640_DVP_0/m_axis_tdata]  [get_bd_pins axi_vdma_0/S_AXIS_S2MM_tdata]
connect_bd_net [get_bd_pins myOV5640_DVP_0/m_axis_tvalid] [get_bd_pins axi_vdma_0/S_AXIS_S2MM_tvalid]
connect_bd_net [get_bd_pins myOV5640_DVP_0/m_axis_tlast]  [get_bd_pins axi_vdma_0/S_AXIS_S2MM_tlast]
connect_bd_net [get_bd_pins axi_vdma_0/S_AXIS_S2MM_tready] [get_bd_pins myOV5640_DVP_0/m_axis_tready]
connect_bd_net [get_bd_pins tkeep_ones/dout] [get_bd_pins axi_vdma_0/S_AXIS_S2MM_tkeep]
# DMA has no tuser port - not needed for raw capture

# DMA memory path
connect_bd_intf_net [get_bd_intf_pins axi_vdma_0/M_AXI_S2MM] [get_bd_intf_pins axi_mem_intercon/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_mem_intercon/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]

validate_bd_design

set ps_gp0 [lindex [get_bd_addr_spaces processing_system7_0/Data] 0]
if {$ps_gp0 == ""} { set ps_gp0 [lindex [get_bd_addr_spaces] 0] }
set dvp_seg [lindex [get_bd_addr_segs myOV5640_DVP_0/s00_axi/reg0] 0]
if {$dvp_seg != "" && $ps_gp0 != ""} { assign_bd_address -offset 0x40000000 -range 0x10000 -target_address_space $ps_gp0 $dvp_seg -force }
set dma_seg [lindex [get_bd_addr_segs axi_vdma_0/S_AXI_LITE/Reg] 0]
if {$dma_seg != "" && $ps_gp0 != ""} { assign_bd_address -offset 0x40400000 -range 0x10000 -target_address_space $ps_gp0 $dma_seg -force }

set dma_space [lindex [get_bd_addr_spaces axi_vdma_0/Data_S2MM] 0]
set hp0_seg [lindex [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] 0]
if {$dma_space != "" && $hp0_seg != ""} {
    assign_bd_address -offset 0x00000000 -range 0x20000000 -target_address_space $dma_space $hp0_seg -force
    puts "  DMA memory path assigned: 0x00000000-0x20000000"
}
save_bd_design

set bd_file [get_files [file join $PROJECT_DIR OV5640_DVP_HW.srcs sources_1 bd design_1 design_1.bd]]
generate_target all $bd_file
make_wrapper -files $bd_file -top
add_files -norecurse [file join $PROJECT_DIR OV5640_DVP_HW.gen sources_1 bd design_1 hdl design_1_wrapper.v]
set_property top design_1_wrapper [current_fileset]
update_compile_order -fileset sources_1

set xdc_path "C:/Users/czy/fpga-workspace/constraints/pynq_z1_ov5640_dvp_hw.xdc"
set fd [open $xdc_path w]
puts $fd {## Pynq-Z1 OV5640 DVP + SCCB + DMA — updated pinout 2026-05-15
set_property -dict { PACKAGE_PIN R14 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[0] }];
set_property -dict { PACKAGE_PIN P14 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[1] }];
set_property -dict { PACKAGE_PIN N16 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[2] }];
set_property -dict { PACKAGE_PIN M14 IOSTANDARD LVCMOS33 } [get_ports { dbg_led_o[3] }];
# DVP 8-bit data: D0..D7
set_property -dict { PACKAGE_PIN W18 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[0] }];
set_property -dict { PACKAGE_PIN Y16 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[1] }];
set_property -dict { PACKAGE_PIN W19 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[2] }];
set_property -dict { PACKAGE_PIN Y17 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[3] }];
set_property -dict { PACKAGE_PIN V16 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[4] }];
set_property -dict { PACKAGE_PIN W14 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[5] }];
set_property -dict { PACKAGE_PIN W16 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[6] }];
set_property -dict { PACKAGE_PIN Y14 IOSTANDARD LVCMOS33 } [get_ports { cam_data_i[7] }];
# Control: HREF, VSYNC(SYNC), PCLK
set_property -dict { PACKAGE_PIN V12 IOSTANDARD LVCMOS33 } [get_ports { cam_href_i   }];
set_property -dict { PACKAGE_PIN T11 IOSTANDARD LVCMOS33 } [get_ports { cam_vsync_i  }];
set_property -dict { PACKAGE_PIN U18 IOSTANDARD LVCMOS33 } [get_ports { cam_pclk_i   }];
# Camera control outputs: XCLK(FLASH), RST, PWDN
set_property -dict { PACKAGE_PIN U19 IOSTANDARD LVCMOS33 } [get_ports { cam_xclk_o   }];
set_property -dict { PACKAGE_PIN Y19 IOSTANDARD LVCMOS33 } [get_ports { cam_resetb_o }];
set_property -dict { PACKAGE_PIN Y18 IOSTANDARD LVCMOS33 } [get_ports { cam_pwdn_o   }];
# SCCB: SDA, SCL (with pullup)
set_property -dict { PACKAGE_PIN W13 IOSTANDARD LVCMOS33 PULLUP true } [get_ports { ov_sda }];
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 PULLUP true } [get_ports { ov_scl }];
create_clock -name cam_pclk -period 17.857 [get_ports { cam_pclk_i }];
set_clock_groups -asynchronous -group [get_clocks cam_pclk] -group [get_clocks -include_generated_clocks clk_fpga_0];
set_input_delay -clock [get_clocks cam_pclk] -min 0.000 [get_ports { cam_data_i[*] cam_href_i cam_vsync_i }];
set_input_delay -clock [get_clocks cam_pclk] -max 8.000 [get_ports { cam_data_i[*] cam_href_i cam_vsync_i }];
set_false_path -to [get_ports { dbg_led_o[*] }];}
close $fd
remove_files -quiet [get_files -filter {NAME =~ "*pynq_z1_ov5640_dvp_hw.xdc"}]
add_files -fileset constrs_1 $xdc_path

set_property SEVERITY {Warning} [get_drc_checks NSTD-1]
set_property SEVERITY {Warning} [get_drc_checks UCIO-1]

puts "=== BUILD ==="
reset_run synth_1; launch_runs synth_1 -jobs 4; wait_on_run synth_1
reset_run impl_1; launch_runs impl_1 -to_step write_bitstream -jobs 4; wait_on_run impl_1
puts "=== DONE ==="
close_project
