# Definitional proc to organize widgets for parameters.
proc init_gui { IPINST } {
  ipgui::add_param $IPINST -name "Component_Name"
  #Adding Page
  set Page_0 [ipgui::add_page $IPINST -name "Page 0"]
  ipgui::add_param $IPINST -name "OV5640_DEV_ADDR" -parent ${Page_0}
  ipgui::add_param $IPINST -name "SOFT_RESET_WAIT_MS" -parent ${Page_0}
  ipgui::add_param $IPINST -name "SYS_CLK_FREQ_HZ" -parent ${Page_0}


}

proc update_PARAM_VALUE.OV5640_DEV_ADDR { PARAM_VALUE.OV5640_DEV_ADDR } {
	# Procedure called to update OV5640_DEV_ADDR when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.OV5640_DEV_ADDR { PARAM_VALUE.OV5640_DEV_ADDR } {
	# Procedure called to validate OV5640_DEV_ADDR
	return true
}

proc update_PARAM_VALUE.SOFT_RESET_WAIT_MS { PARAM_VALUE.SOFT_RESET_WAIT_MS } {
	# Procedure called to update SOFT_RESET_WAIT_MS when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.SOFT_RESET_WAIT_MS { PARAM_VALUE.SOFT_RESET_WAIT_MS } {
	# Procedure called to validate SOFT_RESET_WAIT_MS
	return true
}

proc update_PARAM_VALUE.SYS_CLK_FREQ_HZ { PARAM_VALUE.SYS_CLK_FREQ_HZ } {
	# Procedure called to update SYS_CLK_FREQ_HZ when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.SYS_CLK_FREQ_HZ { PARAM_VALUE.SYS_CLK_FREQ_HZ } {
	# Procedure called to validate SYS_CLK_FREQ_HZ
	return true
}


proc update_MODELPARAM_VALUE.SYS_CLK_FREQ_HZ { MODELPARAM_VALUE.SYS_CLK_FREQ_HZ PARAM_VALUE.SYS_CLK_FREQ_HZ } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.SYS_CLK_FREQ_HZ}] ${MODELPARAM_VALUE.SYS_CLK_FREQ_HZ}
}

proc update_MODELPARAM_VALUE.SOFT_RESET_WAIT_MS { MODELPARAM_VALUE.SOFT_RESET_WAIT_MS PARAM_VALUE.SOFT_RESET_WAIT_MS } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.SOFT_RESET_WAIT_MS}] ${MODELPARAM_VALUE.SOFT_RESET_WAIT_MS}
}

proc update_MODELPARAM_VALUE.OV5640_DEV_ADDR { MODELPARAM_VALUE.OV5640_DEV_ADDR PARAM_VALUE.OV5640_DEV_ADDR } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.OV5640_DEV_ADDR}] ${MODELPARAM_VALUE.OV5640_DEV_ADDR}
}

