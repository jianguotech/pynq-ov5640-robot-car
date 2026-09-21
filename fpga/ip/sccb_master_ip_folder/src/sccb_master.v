`timescale 1ns / 1ps

// OV5640 SCCB master

module sccb_master #(
    // One SCL bit=4 sub-phases. With 100 MHz clk
    // CLK_DIVIDER=250, bit period 10us, 100 kHz.
    parameter integer CLK_DIVIDER = 250,

    //side-band reset/power-down control.
    parameter integer CAMERA_POWERUP_EN = 1,
    parameter integer SYS_CLK_FREQ_HZ   = 100_000_000,

    // default power-up timing
    parameter integer PWDN_HOLD_US      = 1000,   // keep PWDN=1, RESET_N=0
    parameter integer RESET_HOLD_US     = 1000,   // release PWDN, keep RESET_N=0
    parameter integer POST_RESET_US     = 20000   // release RESET_N
)
(
    input         clk,
    input         rst_n,

    // Command interface. accepted only when cmd_valid && cmd_ready.
    // cmd_rw: 0 = write, 1 = read.
    input         cmd_valid,
    output        cmd_ready,
    input         cmd_rw,
    input  [6:0]  dev_addr,
    input  [15:0] reg_addr,
    input  [7:0]  wr_data,

    output reg [7:0] rd_data,
    output        busy,
    output reg    done,
    output reg    error,
    output reg [3:0] error_code,

    // side-band pins.
    // OV_RESET low-active,
    // OV_PWDN high-active.
    output reg    ov_reset_n,
    output reg    ov_pwdn,
    output reg    camera_ready,

    //SCCB outputs.
    // Connect sioc_* and siod_* to Xilinx OBUFT/IOBUF at top level.
    // *_t = 1 means high-Z/released, *_t = 0 means drive *_o.
    output        sioc_o,
    output        sioc_t,
    output        siod_o,
    input         siod_i,
    output        siod_t
);

    // Constant
    localparam integer EFFECTIVE_DIV = (CLK_DIVIDER < 1) ? 1 : CLK_DIVIDER;
    localparam integer DIV_W = (EFFECTIVE_DIV <= 1) ? 1 : $clog2(EFFECTIVE_DIV);

    localparam integer CYCLES_PER_US_RAW = (SYS_CLK_FREQ_HZ / 1_000_000);
    localparam integer CYCLES_PER_US = (CYCLES_PER_US_RAW < 1) ? 1 : CYCLES_PER_US_RAW;

    localparam integer PWDN_HOLD_CYCLES_RAW  = PWDN_HOLD_US  * CYCLES_PER_US;
    localparam integer RESET_HOLD_CYCLES_RAW = RESET_HOLD_US * CYCLES_PER_US;
    localparam integer POST_RESET_CYCLES_RAW = POST_RESET_US * CYCLES_PER_US;

    localparam integer PWDN_HOLD_CYCLES  = (PWDN_HOLD_CYCLES_RAW  < 1) ? 1 : PWDN_HOLD_CYCLES_RAW;
    localparam integer RESET_HOLD_CYCLES = (RESET_HOLD_CYCLES_RAW < 1) ? 1 : RESET_HOLD_CYCLES_RAW;
    localparam integer POST_RESET_CYCLES = (POST_RESET_CYCLES_RAW < 1) ? 1 : POST_RESET_CYCLES_RAW;

    localparam integer PWR_MAX_0 = (PWDN_HOLD_CYCLES > RESET_HOLD_CYCLES) ? PWDN_HOLD_CYCLES : RESET_HOLD_CYCLES;
    localparam integer PWR_MAX   = (PWR_MAX_0 > POST_RESET_CYCLES) ? PWR_MAX_0 : POST_RESET_CYCLES;
    localparam integer PWR_CNT_W = (PWR_MAX <= 1) ? 1 : $clog2(PWR_MAX + 1);

    localparam [3:0] ERR_NONE  = 4'd0;
    localparam [3:0] ERR_STATE = 4'd1;

    // SCCB transaction FSM states
    localparam [3:0] ST_IDLE      = 4'd0;
    localparam [3:0] ST_START     = 4'd1;
    localparam [3:0] ST_SEND_BYTE = 4'd2;
    localparam [3:0] ST_DC_BIT    = 4'd3;
    localparam [3:0] ST_READ_BYTE = 4'd4;
    localparam [3:0] ST_SEND_NA   = 4'd5;
    localparam [3:0] ST_STOP      = 4'd6;
    localparam [3:0] ST_DONE      = 4'd7;
    localparam [3:0] ST_ERROR     = 4'd8;

    // Transaction sequence stages
    localparam [2:0] SEQ_DEV_ADDR_W = 3'd0;
    localparam [2:0] SEQ_REG_HIGH   = 3'd1;
    localparam [2:0] SEQ_REG_LOW    = 3'd2;
    localparam [2:0] SEQ_WRITE_DATA = 3'd3;
    localparam [2:0] SEQ_DEV_ADDR_R = 3'd4;

    // Power-up FSM states
    localparam [1:0] PWR_HOLD_BOTH     = 2'd0;
    localparam [1:0] PWR_RELEASE_PWDN  = 2'd1;
    localparam [1:0] PWR_POST_RESET    = 2'd2;
    localparam [1:0] PWR_READY         = 2'd3;

    // Registers
    reg [3:0] state;
    reg [2:0] seq_stage;

    reg [DIV_W-1:0] clk_cnt;
    reg [1:0]       phase;
    reg [2:0]       bit_idx;
    reg [7:0]       tx_byte;
    reg [7:0]       rx_byte;

    reg             cmd_rw_r;
    reg [6:0]       dev_addr_r;
    reg [15:0]      reg_addr_r;
    reg [7:0]       wr_data_r;
    reg             read_phase_pending;

    reg             hold_scl_low;
    reg             hold_sda_low;

    reg [1:0]       pwr_state;
    reg [PWR_CNT_W-1:0] pwr_cnt;

    // Interface outputs
    assign cmd_ready = camera_ready && (state == ST_IDLE);
    assign busy = (!camera_ready) || ((state != ST_IDLE) && (state != ST_DONE) && (state != ST_ERROR));

    // Open-drain style outputs: only drive low, never drive high.
    assign sioc_o = 1'b0;
    assign sioc_t = ~hold_scl_low;
    assign siod_o = 1'b0;
    assign siod_t = ~hold_sda_low;

    // OV5640 reset / power-down sequencer
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pwr_state    <= PWR_HOLD_BOTH;
            pwr_cnt      <= {PWR_CNT_W{1'b0}};
            camera_ready <= (CAMERA_POWERUP_EN == 0) ? 1'b1 : 1'b0;
            ov_pwdn      <= (CAMERA_POWERUP_EN == 0) ? 1'b0 : 1'b1; // high = power down
            ov_reset_n   <= (CAMERA_POWERUP_EN == 0) ? 1'b1 : 1'b0; // low = reset
        end else begin
            if (CAMERA_POWERUP_EN == 0) begin
                pwr_state    <= PWR_READY;
                pwr_cnt      <= {PWR_CNT_W{1'b0}};
                camera_ready <= 1'b1;
                ov_pwdn      <= 1'b0;
                ov_reset_n   <= 1'b1;
            end else begin
                case (pwr_state)
                    PWR_HOLD_BOTH: begin
                        // Hold camera in power-down and reset.
                        ov_pwdn      <= 1'b1;
                        ov_reset_n   <= 1'b0;
                        camera_ready <= 1'b0;
                        if (pwr_cnt >= PWDN_HOLD_CYCLES - 1) begin
                            pwr_cnt   <= {PWR_CNT_W{1'b0}};
                            pwr_state <= PWR_RELEASE_PWDN;
                        end else begin
                            pwr_cnt <= pwr_cnt + 1'b1;
                        end
                    end

                    PWR_RELEASE_PWDN: begin
                        // Leave power-down, but keep hardware reset active.
                        ov_pwdn      <= 1'b0;
                        ov_reset_n   <= 1'b0;
                        camera_ready <= 1'b0;
                        if (pwr_cnt >= RESET_HOLD_CYCLES - 1) begin
                            pwr_cnt   <= {PWR_CNT_W{1'b0}};
                            pwr_state <= PWR_POST_RESET;
                        end else begin
                            pwr_cnt <= pwr_cnt + 1'b1;
                        end
                    end

                    PWR_POST_RESET: begin
                        // Release reset, then wait before allowing SCCB access.
                        ov_pwdn      <= 1'b0;
                        ov_reset_n   <= 1'b1;
                        camera_ready <= 1'b0;
                        if (pwr_cnt >= POST_RESET_CYCLES - 1) begin
                            pwr_cnt      <= {PWR_CNT_W{1'b0}};
                            pwr_state    <= PWR_READY;
                            camera_ready <= 1'b1;
                        end else begin
                            pwr_cnt <= pwr_cnt + 1'b1;
                        end
                    end

                    default: begin
                        ov_pwdn      <= 1'b0;
                        ov_reset_n   <= 1'b1;
                        camera_ready <= 1'b1;
                        pwr_cnt      <= {PWR_CNT_W{1'b0}};
                        pwr_state    <= PWR_READY;
                    end
                endcase
            end
        end
    end

    // SCCB transaction FSM
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state        <= ST_IDLE;
            seq_stage    <= SEQ_DEV_ADDR_W;
            clk_cnt      <= {DIV_W{1'b0}};
            phase        <= 2'd0;
            bit_idx      <= 3'd7;
            tx_byte      <= 8'h00;
            rx_byte      <= 8'h00;
            cmd_rw_r     <= 1'b0;
            dev_addr_r   <= 7'h00;
            reg_addr_r   <= 16'h0000;
            wr_data_r    <= 8'h00;
            read_phase_pending <= 1'b0;
            rd_data      <= 8'h00;
            done         <= 1'b0;
            error        <= 1'b0;
            error_code   <= ERR_NONE;
            hold_scl_low <= 1'b0;
            hold_sda_low <= 1'b0;
        end else begin
            done  <= 1'b0;
            error <= 1'b0;

            case (state)
                ST_IDLE: begin
                    clk_cnt      <= {DIV_W{1'b0}};
                    phase        <= 2'd0;
                    hold_scl_low <= 1'b0;
                    hold_sda_low <= 1'b0;
                    error_code   <= ERR_NONE;

                    if (cmd_valid && camera_ready) begin
                        cmd_rw_r   <= cmd_rw;
                        dev_addr_r <= dev_addr;
                        reg_addr_r <= reg_addr;
                        wr_data_r  <= wr_data;
                        read_phase_pending <= 1'b0;
                        tx_byte    <= {dev_addr, 1'b0}; // write address phase
                        bit_idx    <= 3'd7;
                        seq_stage  <= SEQ_DEV_ADDR_W;
                        state      <= ST_START;
                    end
                end

                ST_DONE: begin
                    hold_scl_low <= 1'b0;
                    hold_sda_low <= 1'b0;
                    done         <= 1'b1;
                    state        <= ST_IDLE;
                end

                ST_ERROR: begin
                    hold_scl_low <= 1'b0;
                    hold_sda_low <= 1'b0;
                    error        <= 1'b1;
                    state        <= ST_IDLE;
                end

                default: begin
                    if (clk_cnt == EFFECTIVE_DIV - 1) begin
                        clk_cnt <= {DIV_W{1'b0}};

                        case (state)
                            // SDA falls while SCL is high.
                            ST_START: begin
                                case (phase)
                                    2'd0: begin
                                        hold_scl_low <= 1'b0; // release SCL high
                                        hold_sda_low <= 1'b0; // release SDA high
                                        phase        <= 2'd1;
                                    end
                                    2'd1: begin
                                        hold_scl_low <= 1'b0;
                                        hold_sda_low <= 1'b1; // SDA low while SCL high
                                        phase        <= 2'd2;
                                    end
                                    default: begin
                                        hold_scl_low <= 1'b1; // pull SCL low before data bits
                                        hold_sda_low <= 1'b1;
                                        phase        <= 2'd0;
                                        bit_idx      <= 3'd7;
                                        state        <= ST_SEND_BYTE;
                                    end
                                endcase
                            end

                            // Send 8 bits MSB first. SDA changes while SCL is low.
                            ST_SEND_BYTE: begin
                                case (phase)
                                    2'd0: begin
                                        hold_scl_low <= 1'b1;
                                        hold_sda_low <= ~tx_byte[bit_idx]; // 0: drive low, 1: release
                                        phase        <= 2'd1;
                                    end
                                    2'd1: begin
                                        hold_scl_low <= 1'b0; // SCL high, slave samples
                                        phase        <= 2'd2;
                                    end
                                    2'd2: begin
                                        phase <= 2'd3; // high hold time
                                    end
                                    default: begin
                                        hold_scl_low <= 1'b1;
                                        phase        <= 2'd0;
                                        if (bit_idx == 3'd0) begin
                                            state <= ST_DC_BIT;
                                        end else begin
                                            bit_idx <= bit_idx - 3'd1;
                                        end
                                    end
                                endcase
                            end

                            // SCCB ninth bit after a master-written byte.
                            // In SCCB this is a Don't-Care bit
                            ST_DC_BIT: begin
                                case (phase)
                                    2'd0: begin
                                        hold_scl_low <= 1'b1;
                                        hold_sda_low <= 1'b0; // release SDA
                                        phase        <= 2'd1;
                                    end
                                    2'd1: begin
                                        hold_scl_low <= 1'b0;
                                        phase        <= 2'd2;
                                    end
                                    2'd2: begin
                                        phase <= 2'd3;
                                    end
                                    default: begin
                                        hold_scl_low <= 1'b1;
                                        phase        <= 2'd0;

                                        case (seq_stage)
                                            SEQ_DEV_ADDR_W: begin
                                                tx_byte   <= reg_addr_r[15:8];
                                                bit_idx   <= 3'd7;
                                                seq_stage <= SEQ_REG_HIGH;
                                                state     <= ST_SEND_BYTE;
                                            end

                                            SEQ_REG_HIGH: begin
                                                tx_byte   <= reg_addr_r[7:0];
                                                bit_idx   <= 3'd7;
                                                seq_stage <= SEQ_REG_LOW;
                                                state     <= ST_SEND_BYTE;
                                            end

                                            SEQ_REG_LOW: begin
                                                bit_idx <= 3'd7;
                                                if (cmd_rw_r) begin
                                                    // Read transaction uses write-address phase first,
                                                    // then STOP + START + read-address phase.
                                                    read_phase_pending <= 1'b1;
                                                    state              <= ST_STOP;
                                                end else begin
                                                    tx_byte   <= wr_data_r;
                                                    seq_stage <= SEQ_WRITE_DATA;
                                                    state     <= ST_SEND_BYTE;
                                                end
                                            end

                                            SEQ_WRITE_DATA: begin
                                                state <= ST_STOP;
                                            end

                                            SEQ_DEV_ADDR_R: begin
                                                rx_byte <= 8'h00;
                                                bit_idx <= 3'd7;
                                                state   <= ST_READ_BYTE;
                                            end

                                            default: begin
                                                error_code <= ERR_STATE;
                                                state      <= ST_ERROR;
                                            end
                                        endcase
                                    end
                                endcase
                            end

                            // Read 8 bits MSB first. Master releases SDA, camera drives SDA.
                            ST_READ_BYTE: begin
                                case (phase)
                                    2'd0: begin
                                        hold_scl_low <= 1'b1;
                                        hold_sda_low <= 1'b0; // release SDA for slave data
                                        phase        <= 2'd1;
                                    end
                                    2'd1: begin
                                        hold_scl_low <= 1'b0;
                                        phase        <= 2'd2;
                                    end
                                    2'd2: begin
                                        rx_byte[bit_idx] <= siod_i;
                                        phase            <= 2'd3;
                                    end
                                    default: begin
                                        hold_scl_low <= 1'b1;
                                        phase        <= 2'd0;
                                        if (bit_idx == 3'd0) begin
                                            state <= ST_SEND_NA;
                                        end else begin
                                            bit_idx <= bit_idx - 3'd1;
                                        end
                                    end
                                endcase
                            end

                            // SCCB NA bit after a read byte. Master drives/sends NA=1 by releasing SDA.
                            ST_SEND_NA: begin
                                case (phase)
                                    2'd0: begin
                                        hold_scl_low <= 1'b1;
                                        hold_sda_low <= 1'b0; // release SDA = logic 1 with pull-up
                                        phase        <= 2'd1;
                                    end
                                    2'd1: begin
                                        hold_scl_low <= 1'b0;
                                        phase        <= 2'd2;
                                    end
                                    2'd2: begin
                                        phase <= 2'd3;
                                    end
                                    default: begin
                                        hold_scl_low <= 1'b1;
                                        hold_sda_low <= 1'b0;
                                        rd_data      <= rx_byte;
                                        phase        <= 2'd0;
                                        state        <= ST_STOP;
                                    end
                                endcase
                            end

                            // SDA rises while SCL is high.
                            ST_STOP: begin
                                case (phase)
                                    2'd0: begin
                                        hold_scl_low <= 1'b1;
                                        hold_sda_low <= 1'b1;
                                        phase        <= 2'd1;
                                    end
                                    2'd1: begin
                                        hold_scl_low <= 1'b0; // release SCL high while SDA low
                                        hold_sda_low <= 1'b1;
                                        phase        <= 2'd2;
                                    end
                                    default: begin
                                        hold_scl_low <= 1'b0;
                                        hold_sda_low <= 1'b0; // release SDA high: STOP
                                        phase        <= 2'd0;
                                        if (read_phase_pending) begin
                                            read_phase_pending <= 1'b0;
                                            tx_byte            <= {dev_addr_r, 1'b1};
                                            seq_stage          <= SEQ_DEV_ADDR_R;
                                            bit_idx            <= 3'd7;
                                            state              <= ST_START;
                                        end else begin
                                            state <= ST_DONE;
                                        end
                                    end
                                endcase
                            end

                            default: begin
                                error_code <= ERR_STATE;
                                state      <= ST_ERROR;
                            end
                        endcase
                    end else begin
                        clk_cnt <= clk_cnt + 1'b1;
                    end
                end
            endcase
        end
    end

endmodule
