// SDA IOBUF bridge - merges O/T/I into single inout
module sda_iobuf (
    input  wire sda_i_fpga,  // FPGA → pin (output data)
    input  wire sda_t_fpga,  // Tristate: 1 = high-Z, 0 = drive
    output wire sda_o_fpga,  // pin → FPGA (input data)
    inout  wire sda_pin      // Physical bidirectional pin
);
    IOBUF #(.DRIVE(12), .SLEW("SLOW")) iobuf_inst (
        .I  (sda_i_fpga),
        .T  (sda_t_fpga),
        .O  (sda_o_fpga),
        .IO (sda_pin)
    );
endmodule
