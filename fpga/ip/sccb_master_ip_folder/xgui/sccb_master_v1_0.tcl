# Definitional proc to organize widgets for parameters.
proc init_gui { IPINST } {
  ipgui::add_param $IPINST -name "Component_Name"
  #Adding Page
  set Page_0 [ipgui::add_page $IPINST -name "Page 0"]
  ipgui::add_param $IPINST -name "CAMERA_POWERUP_EN" -parent ${Page_0}
  ipgui::add_param $IPINST -name "CLK_DIVIDER" -parent ${Page_0}
  ipgui::add_param $IPINST -name "POST_RESET_US" -parent ${Page_0}
  ipgui::add_param $IPINST -name "PWDN_HOLD_US" -parent ${Page_0}
  ipgui::add_param $IPINST -name "RESET_HOLD_US" -parent ${Page_0}
  ipgui::add_param $IPINST -name "SYS_CLK_FREQ_HZ" -parent ${Page_0}


}

proc update_PARAM_VALUE.CAMERA_POWERUP_EN { PARAM_VALUE.CAMERA_POWERUP_EN } {
	# Procedure called to update CAMERA_POWERUP_EN when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.CAMERA_POWERUP_EN { PARAM_VALUE.CAMERA_POWERUP_EN } {
	# Procedure called to validate CAMERA_POWERUP_EN
	return true
}

proc update_PARAM_VALUE.CLK_DIVIDER { PARAM_VALUE.CLK_DIVIDER } {
	# Procedure called to update CLK_DIVIDER when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.CLK_DIVIDER { PARAM_VALUE.CLK_DIVIDER } {
	# Procedure called to validate CLK_DIVIDER
	return true
}

proc update_PARAM_VALUE.POST_RESET_US { PARAM_VALUE.POST_RESET_US } {
	# Procedure called to update POST_RESET_US when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.POST_RESET_US { PARAM_VALUE.POST_RESET_US } {
	# Procedure called to validate POST_RESET_US
	return true
}

proc update_PARAM_VALUE.PWDN_HOLD_US { PARAM_VALUE.PWDN_HOLD_US } {
	# Procedure called to update PWDN_HOLD_US when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.PWDN_HOLD_US { PARAM_VALUE.PWDN_HOLD_US } {
	# Procedure called to validate PWDN_HOLD_US
	return true
}

proc update_PARAM_VALUE.RESET_HOLD_US { PARAM_VALUE.RESET_HOLD_US } {
	# Procedure called to update RESET_HOLD_US when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.RESET_HOLD_US { PARAM_VALUE.RESET_HOLD_US } {
	# Procedure called to validate RESET_HOLD_US
	return true
}

proc update_PARAM_VALUE.SYS_CLK_FREQ_HZ { PARAM_VALUE.SYS_CLK_FREQ_HZ } {
	# Procedure called to update SYS_CLK_FREQ_HZ when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.SYS_CLK_FREQ_HZ { PARAM_VALUE.SYS_CLK_FREQ_HZ } {
	# Procedure called to validate SYS_CLK_FREQ_HZ
	return true
}


proc update_MODELPARAM_VALUE.CLK_DIVIDER { MODELPARAM_VALUE.CLK_DIVIDER PARAM_VALUE.CLK_DIVIDER } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.CLK_DIVIDER}] ${MODELPARAM_VALUE.CLK_DIVIDER}
}

proc update_MODELPARAM_VALUE.CAMERA_POWERUP_EN { MODELPARAM_VALUE.CAMERA_POWERUP_EN PARAM_VALUE.CAMERA_POWERUP_EN } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.CAMERA_POWERUP_EN}] ${MODELPARAM_VALUE.CAMERA_POWERUP_EN}
}

proc update_MODELPARAM_VALUE.SYS_CLK_FREQ_HZ { MODELPARAM_VALUE.SYS_CLK_FREQ_HZ PARAM_VALUE.SYS_CLK_FREQ_HZ } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.SYS_CLK_FREQ_HZ}] ${MODELPARAM_VALUE.SYS_CLK_FREQ_HZ}
}

proc update_MODELPARAM_VALUE.PWDN_HOLD_US { MODELPARAM_VALUE.PWDN_HOLD_US PARAM_VALUE.PWDN_HOLD_US } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.PWDN_HOLD_US}] ${MODELPARAM_VALUE.PWDN_HOLD_US}
}

proc update_MODELPARAM_VALUE.RESET_HOLD_US { MODELPARAM_VALUE.RESET_HOLD_US PARAM_VALUE.RESET_HOLD_US } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.RESET_HOLD_US}] ${MODELPARAM_VALUE.RESET_HOLD_US}
}

proc update_MODELPARAM_VALUE.POST_RESET_US { MODELPARAM_VALUE.POST_RESET_US PARAM_VALUE.POST_RESET_US } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.POST_RESET_US}] ${MODELPARAM_VALUE.POST_RESET_US}
}

