USE [HlsKeyControl];
GO

CREATE TABLE dbo.Meters (
    meter_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_Meters
        PRIMARY KEY
        DEFAULT NEWSEQUENTIALID(),

    serial_number NVARCHAR(100) NOT NULL,

    status VARCHAR(40) NOT NULL
        CONSTRAINT DF_Meters_Status
        DEFAULT 'new',

    created_at DATETIME2(3) NOT NULL
        CONSTRAINT DF_Meters_CreatedAt
        DEFAULT SYSUTCDATETIME(),

    current_hls_key_id UNIQUEIDENTIFIER NULL,

    row_version ROWVERSION NOT NULL,

    CONSTRAINT UQ_Meters_SerialNumber
        UNIQUE (serial_number),

    CONSTRAINT CK_Meters_Status
        CHECK (
            status IN (
                'new',
                'personalized',
                'operation_unconfirmed',
                'reset',
                'hls_state_unknown'
            )
        )
);
GO

CREATE TABLE dbo.Operations (
    operation_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_Operations
        PRIMARY KEY
        DEFAULT NEWSEQUENTIALID(),

    meter_id UNIQUEIDENTIFIER NOT NULL,
    operation_type VARCHAR(40) NOT NULL,
    description NVARCHAR(500) NOT NULL,

    created_at DATETIME2(3) NOT NULL
        CONSTRAINT DF_Operations_CreatedAt
        DEFAULT SYSUTCDATETIME(),

    CONSTRAINT FK_Operations_Meters
        FOREIGN KEY (meter_id)
        REFERENCES dbo.Meters (meter_id),

    CONSTRAINT CK_Operations_Type
        CHECK (
            operation_type IN (
                'meter_registered',
                'meter_found',
                'hls_reserved',
                'hls_reset'
            )
        )
);
GO

CREATE INDEX IX_Operations_MeterId_CreatedAt
    ON dbo.Operations (meter_id, created_at DESC);
GO

CREATE TABLE dbo.HlsKeys (
    hls_key_id UNIQUEIDENTIFIER NOT NULL
        CONSTRAINT PK_HlsKeys
        PRIMARY KEY
        DEFAULT NEWSEQUENTIALID(),

    meter_id UNIQUEIDENTIFIER NOT NULL,
    status VARCHAR(40) NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    encrypted_value VARBINARY(512) NULL,

    created_at DATETIME2(3) NOT NULL
        CONSTRAINT DF_HlsKeys_CreatedAt
        DEFAULT SYSUTCDATETIME(),

    retired_at DATETIME2(3) NULL,

    CONSTRAINT FK_HlsKeys_Meters
        FOREIGN KEY (meter_id)
        REFERENCES dbo.Meters (meter_id),

    CONSTRAINT UQ_HlsKeys_Fingerprint
        UNIQUE (fingerprint),

    CONSTRAINT CK_HlsKeys_Status
        CHECK (
            status IN (
                'reserved',
                'active',
                'retired',
                'unconfirmed',
                'failed'
            )
        )
);
GO

CREATE INDEX IX_HlsKeys_MeterId_CreatedAt
    ON dbo.HlsKeys (meter_id, created_at DESC);
GO

ALTER TABLE dbo.Meters
ADD CONSTRAINT FK_Meters_CurrentHlsKey
    FOREIGN KEY (current_hls_key_id)
    REFERENCES dbo.HlsKeys (hls_key_id);
GO