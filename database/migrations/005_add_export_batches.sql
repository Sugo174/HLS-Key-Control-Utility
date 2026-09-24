USE [HlsKeyControl];
GO

CREATE TABLE dbo.ExportBatches (
    export_batch_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_ExportBatches
        PRIMARY KEY
        DEFAULT NEWSEQUENTIALID(),

    batch_number NVARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL
        CONSTRAINT DF_ExportBatches_Status
        DEFAULT 'created',

    created_at DATETIME2(3) NOT NULL
        CONSTRAINT DF_ExportBatches_CreatedAt
        DEFAULT SYSUTCDATETIME(),

    created_by NVARCHAR(128) NOT NULL,
    row_count INT NOT NULL
        CONSTRAINT DF_ExportBatches_RowCount
        DEFAULT 0,

    file_name NVARCHAR(260) NULL,
    file_sha256 CHAR(64) NULL,
    completed_at DATETIME2(3) NULL,

    CONSTRAINT UQ_ExportBatches_Number
        UNIQUE (batch_number),

    CONSTRAINT CK_ExportBatches_Status
        CHECK (status IN ('created', 'completed', 'failed'))
);
GO

CREATE TABLE dbo.ExportBatchItems (
    export_batch_item_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_ExportBatchItems
        PRIMARY KEY
        DEFAULT NEWSEQUENTIALID(),

    export_batch_id UNIQUEIDENTIFIER NOT NULL,
    meter_id UNIQUEIDENTIFIER NOT NULL,
    hls_key_id UNIQUEIDENTIFIER NOT NULL,

    serial_number NVARCHAR(100) NOT NULL,
    personalized_at DATETIME2(3) NOT NULL,

    CONSTRAINT FK_ExportBatchItems_Batch
        FOREIGN KEY (export_batch_id)
        REFERENCES dbo.ExportBatches (export_batch_id),

    CONSTRAINT FK_ExportBatchItems_Meter
        FOREIGN KEY (meter_id)
        REFERENCES dbo.Meters (meter_id),

    CONSTRAINT FK_ExportBatchItems_HlsKey
        FOREIGN KEY (hls_key_id)
        REFERENCES dbo.HlsKeys (hls_key_id),

    CONSTRAINT UQ_ExportBatchItems_HlsKey
        UNIQUE (hls_key_id)
);
GO

CREATE INDEX IX_ExportBatchItems_BatchId
    ON dbo.ExportBatchItems (export_batch_id);
GO

ALTER TABLE dbo.Operations
DROP CONSTRAINT CK_Operations_Type;
GO

ALTER TABLE dbo.Operations
ADD CONSTRAINT CK_Operations_Type
CHECK (
    operation_type IN (
        'meter_registered',
        'meter_found',
        'hls_reserved',
        'hls_reset',
        'hls_activated',
        'hls_write_unconfirmed',
        'hls_write_rejected',
        'export_batch_created',
        'export_batch_completed'
    )
);
GO