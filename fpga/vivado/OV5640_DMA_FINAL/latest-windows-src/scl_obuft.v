// SCL tristate output buffer for open-drain SCCB
module scl_obuft (
    input  wire scl_i,    // FPGA -> pin (always 0 for open-drain)
    input  wire scl_t,    // 0=drive, 1=high-Z (release to pull-up)
    output wire scl_pin   // Physical SCL pin
);
    OBUFT #(.DRIVE(12), .SLEW("SLOW")) obuft_inst (
        .I(scl_i),
        .T(scl_t),
        .O(scl_pin)
    );
endmodule
