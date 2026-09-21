// Behavioral model of XPM FIFO async for simulation (FWFT mode)
`timescale 1ns / 1ps

module xpm_fifo_async #(
    parameter integer FIFO_WRITE_DEPTH = 1024,
    parameter integer WRITE_DATA_WIDTH = 18,
    parameter integer READ_DATA_WIDTH  = 18,
    parameter         READ_MODE        = "fwft",
    parameter         FIFO_MEMORY_TYPE = "auto",
    parameter integer CDC_SYNC_STAGES  = 3,
    parameter integer RELATED_CLOCKS   = 0
)(
    input  wire                        rst,
    input  wire                        wr_clk,
    input  wire                        wr_en,
    input  wire [WRITE_DATA_WIDTH-1:0] din,
    output wire                        full,
    output wire                        wr_ack,
    output wire                        overflow,
    output wire                        prog_full,
    output wire [9:0]                  wr_data_count,
    output wire                        almost_full,
    output wire                        wr_rst_busy,
    input  wire                        rd_clk,
    input  wire                        rd_en,
    output wire [READ_DATA_WIDTH-1:0]  dout,
    output wire                        empty,
    output wire                        underflow,
    output wire [9:0]                  rd_data_count,
    output wire                        prog_empty,
    output wire                        almost_empty,
    output wire                        rd_rst_busy,
    output wire                        data_valid,
    input  wire                        sleep,
    input  wire                        injectdbiterr,
    input  wire                        injectsbiterr,
    output wire                        sbiterr,
    output wire                        dbiterr
);

    localparam DEPTH = FIFO_WRITE_DEPTH;
    localparam AW = $clog2(DEPTH);

    reg [WRITE_DATA_WIDTH-1:0] mem [0:DEPTH-1];

    reg [AW-1:0] wr_ptr = 0;
    reg [AW-1:0] rd_ptr = 0;

    reg [WRITE_DATA_WIDTH-1:0] dout_r = 0;
    reg empty_r = 1;
    reg full_r = 0;

    // Write port (wr_clk domain)
    always @(posedge wr_clk) begin
        if (rst) begin
            wr_ptr <= 0;
        end else if (wr_en && !full_r) begin
            mem[wr_ptr] <= din;
            wr_ptr <= wr_ptr + 1;
        end
    end

    // Full detection (instantaneous for simulation)
    always @(*) begin
        full_r = ((wr_ptr + 1) % DEPTH == rd_ptr);
    end

    // FWFT Read port (rd_clk domain)
    always @(posedge rd_clk) begin
        if (rst) begin
            rd_ptr <= 0;
            empty_r <= 1;
            dout_r <= 0;
        end else begin
            if (empty_r) begin
                // FWFT: present data immediately when available
                if (wr_ptr != rd_ptr) begin
                    dout_r <= mem[rd_ptr];
                    empty_r <= 1'b0;
                end
            end else if (rd_en) begin
                // Consume current, pre-fetch next
                rd_ptr <= rd_ptr + 1;
                if (((rd_ptr + 1) % DEPTH) == wr_ptr) begin
                    empty_r <= 1'b1;
                end else begin
                    dout_r <= mem[(rd_ptr + 1) % DEPTH];
                end
            end
        end
    end

    assign dout      = dout_r;
    assign empty     = empty_r;
    assign full      = full_r;
    assign data_valid = rd_en && !empty_r;

    assign wr_ack    = wr_en && !full_r;
    assign overflow  = wr_en && full_r;
    assign underflow = rd_en && empty_r;

    assign prog_full      = 0;
    assign wr_data_count  = 0;
    assign almost_full    = 0;
    assign wr_rst_busy    = 0;
    assign rd_data_count  = 0;
    assign prog_empty     = 0;
    assign almost_empty   = 0;
    assign rd_rst_busy    = 0;
    assign sbiterr        = 0;
    assign dbiterr        = 0;

endmodule
